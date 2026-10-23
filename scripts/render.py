#!/usr/bin/env python3
"""生成图书馆图片：总览封面 + 每本藏书票。

默认 --engine auto：可用 playwright+chromium 时走浏览器渲染，否则自动降级到 Pillow。
"""
import argparse, io, json, math, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from library import PALETTE, color, facts, load, short_title, span, ts
from pages import card_author


SANS = '"PingFang SC","Noto Sans CJK SC","Hiragino Sans GB",sans-serif'
MUTED, FAINT = "#686b70", "rgba(11,11,11,.42)"


def eprint(msg):
    print(msg, file=sys.stderr)


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_pillow():
    try:
        from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps
        return Image, ImageColor, ImageDraw, ImageFont, ImageOps
    except ImportError:
        sys.exit("缺少 Pillow：pip install pillow（也可以先跳过图片生成这一步）")


def font_candidates(extra):
    base = [
        extra,
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/STHeiti Medium.ttc",
    ]
    linux = []
    roots = ["/usr/share/fonts", "/usr/local/share/fonts"]
    keys = ("NotoSerifCJK", "NotoSansCJK", "SourceHanSerif", "SourceHanSans", "wqy", "WenQuanYi")
    for r in roots:
        if not os.path.isdir(r):
            continue
        for d, _, fs in os.walk(r):
            for f in fs:
                if f.lower().endswith((".ttf", ".ttc", ".otf")) and any(k.lower() in f.lower() for k in keys):
                    linux.append(os.path.join(d, f))
    linux.sort(key=lambda p: (next(i for i, k in enumerate(keys) if k.lower() in os.path.basename(p).lower()), "bold" in p.lower()))
    seen, out = set(), []
    for p in base + linux:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def choose_font(ImageFont, specified):
    candidates = font_candidates(specified)
    serif_tags = ("songti", "serif", "sourcehanserif", "noto serif", "notoserif", "simsun")
    sans_tags = ("heiti", "pingfang", "sans", "sourcehansans", "noto sans", "notosans", "wqy")
    chosen_serif, chosen_sans = None, None
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            ImageFont.truetype(p, 48)
        except Exception:
            continue
        pl = p.lower()
        if not chosen_serif and any(t in pl for t in serif_tags):
            chosen_serif = p
        if not chosen_sans and any(t in pl for t in sans_tags):
            chosen_sans = p
        if chosen_serif and chosen_sans:
            break
    chosen = chosen_serif or chosen_sans
    if not chosen:
        sys.exit("未找到可用中文字体。请安装中文字体后重试（Debian/Ubuntu 可执行：apt install fonts-noto-cjk），或用 --font 指定字体路径。")
    return {"serif": chosen_serif or chosen, "sans": chosen_sans or chosen}


def wrap_text(draw, text, font, max_w, max_lines=None):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur.rstrip())
            cur = ""
            continue
        nxt = cur + ch
        if draw.textlength(nxt, font=font) <= max_w or not cur:
            cur = nxt
        else:
            lines.append(cur.rstrip())
            cur = ch
        if max_lines and len(lines) >= max_lines:
            break
    if cur and (not max_lines or len(lines) < max_lines):
        lines.append(cur.rstrip())
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
    return lines


def add_ellipsis(draw, lines, font, max_w):
    if not lines:
        return lines
    while draw.textlength(lines[-1] + "…", font=font) > max_w and lines[-1]:
        lines[-1] = lines[-1][:-1]
    lines[-1] = (lines[-1] or "") + "…"
    return lines


def fit_text_block(draw, text, max_w, make_font, sizes, max_lines):
    best_lines, best_font, clipped = [text], make_font(sizes[-1]), False
    for size in sizes:
        font = make_font(size)
        lines = wrap_text(draw, text, font, max_w, max_lines=max_lines)
        consumed = len("".join(lines))
        if consumed >= len(text.replace("\n", "")):
            return lines, font, False
        best_lines, best_font, clipped = lines, font, True
    return add_ellipsis(draw, best_lines, best_font, max_w), best_font, clipped


def draw_vtext(draw, x, y, text, font, fill, max_h):
    step = font.size + 2
    yy = y
    for ch in text:
        if yy + step > y + max_h:
            break
        draw.text((x, yy), ch, font=font, fill=fill)
        yy += step


def calc_cover_selection(books):
    sel = sorted(books, key=lambda b: -b["readingTime"])[:34]
    order = list(PALETTE)
    sel.sort(key=lambda b: (order.index(b["top"]) if b["top"] in order else 99, -b["readingTime"]))
    return sel


def calc_spine_hw(book):
    h = min((170 + math.sqrt(max(book["readingTime"], 600) / 3600) * 120) * 0.86, 400)
    w = 24 + min(len(book["items"]), 35) * 1.25
    return h, w


def fit_cover_title(name):
    n = max(1, len((name or "").strip()))
    if n <= 8:
        return 68, 1.2
    if n <= 12:
        return 60, 1.24
    if n <= 16:
        return 52, 1.28
    return 46, 1.32


def ghost_spine_slots():
    return [(300, 52), (360, 60), (270, 48), (330, 56), (290, 50), (380, 64), (310, 54), (350, 58)]


def cover_eye_text(fy):
    if not fy:
        return "A PRIVATE LIBRARY"
    return f"A PRIVATE LIBRARY · EST. {fy.year}.{fy.month:02d}"


def build_ghost_spines(remaining_w, gap, scale=1.0, min_width=42):
    slots = ghost_spine_slots()
    ghosts = []
    rest = int(max(0, remaining_w))
    i = 0
    while rest >= min_width:
        h0, w0 = slots[i % len(slots)]
        h = int(round(h0 * scale))
        w = int(round(w0 * scale))
        need = (gap if ghosts else 0) + w
        if need > rest:
            avail = rest - (gap if ghosts else 0)
            if avail >= min_width:
                ghosts.append((h, avail))
                rest = 0
            break
        ghosts.append((h, w))
        rest -= need
        i += 1
    if ghosts and rest > 0:
        add, rem = divmod(rest, len(ghosts))
        ghosts = [(h, w + add + (1 if idx < rem else 0)) for idx, (h, w) in enumerate(ghosts)]
    return ghosts


def cover_html(books, f, name):
    sel = calc_cover_selection(books)
    title_size, title_lh = fit_cover_title(name)
    spines = ""
    real_ws = []
    gap = 4
    shelf_w = 1600 - 96 * 2
    for b in sel:
        h, w = calc_spine_hw(b)
        t = esc(short_title(b["title"])[:9])
        spines += (f'<div class="spine" style="height:{h:.0f}px;width:{w:.0f}px;background:{color(b["top"])}">'
                   f'<i class="a"></i><span style="font-size:{15 if len(t) > 6 else 17}px">{t}</span><i class="b"></i></div>')
        real_ws.append(int(round(w)))
    note = ""
    used_real = sum(real_ws) + max(0, len(real_ws) - 1) * gap
    remaining = max(0, shelf_w - used_real)
    ghosts = build_ghost_spines(remaining, gap, min_width=42)
    if ghosts:
        spines += "".join(f'<div class="ghost" style="height:{h}px;width:{w}px"></div>' for h, w in ghosts)
        ghost_start = used_real + (gap if real_ws else 0)
        ghost_w = sum(w for _, w in ghosts) + max(0, len(ghosts) - 1) * gap
        note = f'<div class="note" style="left:{96 + ghost_start}px;width:{ghost_w}px">书架上还有很多位置</div>'
    used = []
    for b in sel:
        if b["top"] not in used:
            used.append(b["top"])
    legend = "".join(f'<div>{esc(c)}<em style="background:{color(c)}"></em></div>' for c in used)
    fy = ts(f["first"]) if f["first"] else None
    eye = cover_eye_text(fy)
    meta = (f'{fy.year} 年 {fy.month} 月开馆 · ' if fy else "") + f'<b>{f["shelf"]}</b> 本藏书 · <b>{f["highlights"] + f["thoughts"]}</b> 条划线与想法 · <b>{f["hours"]}</b> 小时'
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}} html,body{{width:1600px;height:900px;overflow:hidden}}
body{{background:
radial-gradient(128% 92% at 18% 0%,#313831 0%,rgba(49,56,49,.6) 33%,rgba(26,30,26,.2) 58%,transparent 80%),
radial-gradient(145% 118% at 50% 48%,#1f231f 0%,#171a17 72%,#141714 100%);
font-family:serif;color:#ece5d5;position:relative}}
.head{{position:absolute;left:96px;top:80px;right:470px}} .eye{{font-size:14px;letter-spacing:.45em;color:#a49b86}}
.t{{font-size:{title_size}px;line-height:{title_lh};letter-spacing:.09em;margin-top:16px;font-weight:600;max-width:100%;word-break:break-all}}
.m{{margin-top:22px;font-size:19px;letter-spacing:.14em;color:#b7ae99;line-height:1.5}} .m b{{color:#ece5d5}}
.shelf{{position:absolute;left:96px;right:96px;bottom:118px;height:480px;display:flex;align-items:flex-end;gap:4px}}
.board{{position:absolute;left:72px;right:72px;bottom:96px;height:22px;background:linear-gradient(#5d4834,#3e3023);box-shadow:0 16px 30px rgba(0,0,0,.45)}}
.spine{{display:flex;justify-content:center;padding-top:22px;position:relative;border-radius:2px 2px 0 0;box-shadow:inset -4px 0 0 rgba(0,0,0,.2),inset 3px 0 0 rgba(255,255,255,.07)}}
.spine span{{writing-mode:vertical-rl;letter-spacing:.14em;color:rgba(255,247,232,.9);white-space:nowrap;overflow:hidden;max-height:calc(100% - 50px)}}
.spine i{{position:absolute;left:20%;right:20%;height:2px;background:rgba(255,236,200,.38)}} .spine i.a{{top:10px}} .spine i.b{{bottom:14px}}
.ghost{{border:1.5px dashed rgba(236,229,213,.18);border-bottom:none;border-radius:2px 2px 0 0}}
.note{{position:absolute;bottom:72px;font-size:17px;letter-spacing:.18em;color:#8b857a;text-align:center}}
.legend{{position:absolute;right:96px;top:92px;display:grid;grid-template-columns:repeat(2,auto);column-gap:34px;text-align:right;font-size:13px;letter-spacing:.14em;color:#8f887a;line-height:2.1}}
.legend em{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-left:10px;vertical-align:-1px}}
.foot{{position:absolute;left:96px;bottom:42px;font-size:14px;letter-spacing:.28em;color:#7d766a}}
.foot2{{position:absolute;right:96px;bottom:44px;font-size:12px;letter-spacing:.14em;color:rgba(208,199,182,.58)}}
</style></head><body>
<div class="head"><div class="eye">{esc(eye)}</div><div class="t">{esc(name)}</div><div class="m">{meta}</div></div>
<div class="legend">{legend}</div><div class="board"></div><div class="shelf">{spines}</div>{note}
<div class="foot">书脊高度 = 你在这本书上花的时间 · 厚度 = 你留下的笔记</div><div class="foot2">用 Corvio 整理 · corvio.ai</div>
</body></html>'''


def plate_html(b, c, n, cover_path, name):
    col = color(b["top"])
    f0, f1 = span(b)
    ym = lambda t: f"{ts(t).year}.{ts(t).month:02d}"
    sp = ym(f0) if f0 and ym(f0) == ym(f1) else (f"{ym(f0)} — {ym(f1)}" if f0 else "")
    meta = f'{b["nh"]} 条划线 · {b["nr"]} 条想法 · {b["readingTime"]/3600:.1f} 小时' + (f' · <span style="white-space:nowrap">{sp}</span>' if sp else "")
    q = c.get("quote", "").strip().rstrip("。")
    title = c.get("short") or short_title(b["title"])
    tsize = 52 if len(title) <= 12 else 40
    img = (f'<img class="cover" src="file://{cover_path}">' if cover_path else f'<div class="cover fake" style="background:{col}"><div>{esc(title)}</div></div>')
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}} html,body{{width:1600px;height:640px;overflow:hidden}}
body{{background:#f2ece0;font-family:serif;color:#2b2622;position:relative}}
.frame{{position:absolute;inset:28px;border:1.5px solid #c9bda6}} .frame2{{position:absolute;inset:36px;border:.75px solid #d8cdb8}}
.cover{{position:absolute;left:124px;top:84px;height:472px;width:auto;max-width:360px;border-radius:3px;box-shadow:18px 22px 40px rgba(60,40,20,.3)}}
.fake{{width:320px;padding:48px 36px;color:#f6efe2;font-size:32px;letter-spacing:.1em;line-height:1.5}}
.right{{position:absolute;left:560px;right:120px;top:92px}}
.eye{{font-size:22px;letter-spacing:.36em;color:#9a8c72}}
.title{{font-size:{tsize}px;letter-spacing:.1em;margin-top:22px;font-weight:600;line-height:1.25}}
.author{{font-size:34px;letter-spacing:.1em;color:#6f6556;margin-top:22px}}
.q{{margin-top:38px;font-size:{30 if len(q) <= 30 else 26}px;line-height:1.7;letter-spacing:.04em;color:#3a322b;border-left:3px solid {col};padding-left:26px}}
.bottom{{position:absolute;left:560px;right:120px;bottom:92px;display:flex;align-items:flex-end;gap:44px}}
.meta,.mark{{font-family:{SANS};color:{MUTED}}}
.meta{{flex:1 1 auto;min-width:0;font-size:27px;letter-spacing:.04em;line-height:1.45;overflow-wrap:anywhere}}
.mark{{flex:0 0 auto;display:flex;flex-direction:column;align-items:flex-end;gap:8px}}
.no{{font-size:25px;letter-spacing:.16em}}
.wm{{font-size:21px;letter-spacing:.04em;color:{FAINT}}}
</style></head><body><div class="frame"></div><div class="frame2"></div>{img}
<div class="right"><div class="eye">EX LIBRIS · {esc(name)}</div><div class="title">{esc(title)}</div><div class="author">{esc(card_author(b["author"]))}</div>
<div class="q">「{esc(q)}。」</div></div><div class="bottom"><div class="meta">{meta}</div><div class="mark"><div class="no">No.{n:03d}</div><div class="wm">用 Corvio 整理 · corvio.ai</div></div></div></body></html>'''


def read_jobs(data_dir, work_dir, include_private):
    _, books = load(data_dir, include_private)
    by = {b["id"]: b for b in books}
    f = facts(data_dir, include_private)
    sel = json.load(open(os.path.join(work_dir, "selection.json"), encoding="utf-8"))
    cur = json.load(open(os.path.join(work_dir, "curation.json"), encoding="utf-8"))
    return books, by, f, sel, cur


def valid_image_bytes(data, Image):
    if len(data) < 3000:
        return False
    try:
        im = Image.open(io.BytesIO(data))
        im.verify()
        return True
    except Exception:
        return False


def ensure_cover(book, cov_dir, Image):
    if not book.get("cover"):
        return None
    cp = os.path.join(cov_dir, f'{book["id"]}.jpg')
    if os.path.exists(cp):
        try:
            Image.open(cp).verify()
            return cp
        except Exception:
            try:
                os.remove(cp)
            except OSError:
                pass
    urls = [book["cover"].replace("/t6_", "/t9_").replace("/s_", "/t9_"), book["cover"]]
    for u in urls:
        try:
            data = urllib.request.urlopen(u, timeout=20).read()
            if valid_image_bytes(data, Image):
                with open(cp, "wb") as f:
                    f.write(data)
                return cp
        except Exception:
            continue
    return None


def get_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright, None
    except Exception as e:
        return None, str(e)


def can_launch_playwright(sync_playwright):
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            br.close()
        return True, ""
    except Exception as e:
        return False, str(e)


def choose_engine(req):
    sync_playwright, err = get_playwright()
    if req == "pil":
        eprint("render: 使用 pil 引擎")
        return "pil", None
    if req == "playwright":
        if not sync_playwright:
            sys.exit(f"playwright 不可用：{err}。请安装 playwright 并执行 python3 -m playwright install chromium，或改用 --engine pil。")
        ok, why = can_launch_playwright(sync_playwright)
        if not ok:
            sys.exit(f"Chromium 启动失败：{why}。请安装/修复 chromium，或改用 --engine pil。")
        eprint("render: 使用 playwright 引擎")
        return "playwright", sync_playwright
    if sync_playwright:
        ok, why = can_launch_playwright(sync_playwright)
        if ok:
            eprint("render: 使用 playwright 引擎（auto）")
            return "playwright", sync_playwright
        eprint(f"render: 使用 pil 引擎（auto 降级，chromium 启动失败：{why}）")
        return "pil", None
    eprint(f"render: 使用 pil 引擎（auto 降级，playwright 不可用：{err}）")
    return "pil", None


def text_height(draw, font):
    box = draw.textbbox((0, 0), "字", font=font)
    return box[3] - box[1]


def make_cover_background(Image, ImageColor):
    W, H = 3200, 1800
    dark = ImageColor.getrgb("#141714")
    mid = ImageColor.getrgb("#1f231f")
    glow = ImageColor.getrgb("#313831")
    img = Image.new("RGB", (W, H))
    px = img.load()
    gx, gy = int(W * 0.18), 0
    rx, ry = W * 1.3, H * 1.05
    cx, cy = W * 0.5, H * 0.54
    for y in range(H):
        for x in range(W):
            dx = (x - gx) / rx
            dy = (y - gy) / ry
            g = min(1.0, math.sqrt(dx * dx + dy * dy))
            base = [int(glow[i] * (1 - g) + mid[i] * g) for i in range(3)]
            ex = (x - cx) / (W * 0.62)
            ey = (y - cy) / (H * 0.72)
            vignette = min(1.0, math.sqrt(ex * ex + ey * ey))
            dim = 0.08 * vignette
            out = [int(base[i] * (1 - dim) + dark[i] * dim) for i in range(3)]
            px[x, y] = tuple(out)
    return img


COVER_PIL = dict(eye="#a49b86", title="#ece5d5", meta="#b7ae99", legend="#8f887a", ghost="#5f5a51", note="#8b857a",
                 board="#4b3a2b", foot="#7d766a", wm="#a59b8d")


def render_cover_pil(path, books, f, name, fonts, Image, ImageColor, ImageDraw, ImageFont):
    W, H = 3200, 1800
    P = COVER_PIL
    img = make_cover_background(Image, ImageColor)
    draw = ImageDraw.Draw(img)

    serif = lambda s: ImageFont.truetype(fonts["serif"], s)
    sans = lambda s: ImageFont.truetype(fonts["sans"], s)
    fy = ts(f["first"]) if f["first"] else None
    meta = (f'{fy.year} 年 {fy.month} 月开馆 · ' if fy else "") + f'{f["shelf"]} 本藏书 · {f["highlights"] + f["thoughts"]} 条划线与想法 · {f["hours"]} 小时'
    draw.text((192, 160), cover_eye_text(fy), font=sans(28), fill=P["eye"])
    title_font_size = 128
    title_font = serif(title_font_size)
    title_x, title_y = 192, 220
    title_max_w = 1940
    while title_font_size > 82 and draw.textlength(name, font=title_font) > title_max_w:
        title_font_size -= 4
        title_font = serif(title_font_size)
    draw.text((title_x, title_y), name, font=title_font, fill=P["title"])
    title_box = draw.textbbox((title_x, title_y), name, font=title_font)
    meta_y = title_box[3] + 38
    draw.text((192, meta_y), meta, font=serif(38), fill=P["meta"])

    sel = calc_cover_selection(books)
    used = []
    for b in sel:
        if b["top"] not in used:
            used.append(b["top"])
    for i, cat in enumerate(used):
        row, col = divmod(i, 2)
        rx = W - 192 - (1 - col) * 220
        ty = 182 + row * 58
        draw.text((rx - 40 - draw.textlength(cat, font=serif(26)), ty), cat, font=serif(26), fill=P["legend"])
        draw.rounded_rectangle((rx - 20, ty + 12, rx, ty + 32), radius=3, fill=color(cat))

    shelf_left, shelf_bottom = 192, 1364
    shelf_right = W - 192
    x = shelf_left
    for b in sel:
        hh, ww = calc_spine_hw(b)
        h, w = int(round(hh * 2)), int(round(ww * 2))
        y = shelf_bottom - h
        if x + w > shelf_right:
            break
        draw.rounded_rectangle((x, y, x + w, y + h), radius=3, fill=color(b["top"]))
        draw.line((x + int(w * 0.2), y + 20, x + int(w * 0.8), y + 20), fill="#d6c1a0", width=3)
        draw.line((x + int(w * 0.2), y + h - 28, x + int(w * 0.8), y + h - 28), fill="#d6c1a0", width=3)
        t = short_title(b["title"])[:9]
        fz = 34 if len(t) <= 6 else 30
        draw_vtext(draw, x + w // 2 - fz // 2, y + 48, t, serif(fz), "#f8efde", h - 88)
        x += w + 8
    remaining = max(0, shelf_right - x)
    ghosts = build_ghost_spines(remaining, gap=8, scale=2.0, min_width=84)
    if ghosts:
        ghost_start = x
        for gh, gw in ghosts:
            y = shelf_bottom - gh
            draw.rounded_rectangle((x, y, x + gw, y + gh), radius=3, outline=P["ghost"], width=3)
            x += gw + 8
        ghost_end = x - 8
        note = "书架上还有很多位置"
        note_font = serif(34)
        note_w = draw.textlength(note, font=note_font)
        note_center = (ghost_start + ghost_end) / 2
        note_x = int(max(192, min(W - 192 - note_w, note_center - note_w / 2)))
        draw.text((note_x, 1450), note, font=note_font, fill=P["note"])
    draw.rectangle((144, 1366, W - 144, 1410), fill=P["board"])
    draw.text((192, 1680), "书脊高度 = 你在这本书上花的时间 · 厚度 = 你留下的笔记", font=serif(30), fill=P["foot"])
    wm = "用 Corvio 整理 · corvio.ai"
    draw.text((W - 192 - draw.textlength(wm, font=sans(24)), 1680), wm, font=sans(24), fill=P["wm"])
    img.save(path, "JPEG", quality=88)


def render_plate_pil(path, b, c, n, cover_path, name, fonts, Image, ImageDraw, ImageFont, ImageOps):
    W, H = 3200, 1280
    img = Image.new("RGB", (W, H), "#f2ece0")
    draw = ImageDraw.Draw(img)
    serif = lambda s: ImageFont.truetype(fonts["serif"], s)
    sans = lambda s: ImageFont.truetype(fonts["sans"], s)
    col = color(b["top"])
    draw.rectangle((56, 56, W - 56, H - 56), outline="#c9bda6", width=3)
    draw.rectangle((72, 72, W - 72, H - 72), outline="#d8cdb8", width=2)

    cx, cy, cw, ch = 248, 168, 720, 944
    used_cover = False
    if cover_path and os.path.exists(cover_path):
        try:
            cover = Image.open(cover_path).convert("RGB")
            cover = ImageOps.contain(cover, (cw, ch))
            px = cx + (cw - cover.width) // 2
            py = cy + (ch - cover.height) // 2
            img.paste(cover, (px, py))
            used_cover = True
        except Exception:
            used_cover = False
    if not used_cover:
        draw.rounded_rectangle((cx, cy, cx + cw, cy + ch), radius=8, fill=col)
        ft = serif(56)
        lines = wrap_text(draw, c.get("short") or short_title(b["title"]), ft, cw - 96, max_lines=10)
        yy = cy + 96
        for ln in lines:
            draw.text((cx + 48, yy), ln, font=ft, fill="#f6efe2")
            yy += text_height(draw, ft) + 10

    f0, f1 = span(b)
    ym = lambda t: f"{ts(t).year}.{ts(t).month:02d}"
    sp = ym(f0) if f0 and ym(f0) == ym(f1) else (f"{ym(f0)} — {ym(f1)}" if f0 else "")
    meta = f'{b["nh"]} 条划线 · {b["nr"]} 条想法 · {b["readingTime"]/3600:.1f} 小时' + (f" · {sp}" if sp else "")
    title = c.get("short") or short_title(b["title"])
    tsize = 104 if len(title) <= 12 else 80
    qraw = (c.get("quote") or "").strip().rstrip("。")

    rx, rw = 1120, 1840
    draw.text((rx, 184), f"EX LIBRIS · {name}", font=sans(45), fill="#9a8c72")
    title_font = serif(tsize)
    ttl = wrap_text(draw, title, title_font, rw, max_lines=2)
    ty = 242
    for ln in ttl:
        draw.text((rx, ty), ln, font=title_font, fill="#2b2622")
        ty += text_height(draw, title_font) + 8
    author_font = serif(72)
    author_y = ty + 24
    draw.text((rx, author_y), card_author(b["author"]), font=author_font, fill="#6f6556")

    qx, qw = rx, rw - 40
    qy = author_y + text_height(draw, author_font) + 42
    meta_y = H - 246
    qh = max(200, meta_y - qy - 34)
    best_lines, best_font, clipped = [], serif(54), False
    for size in (64, 60, 56, 52, 48, 44):
        fnt = serif(size)
        lines = wrap_text(draw, f"「{qraw}。」", fnt, qw - 56)
        lh = text_height(draw, fnt) + int(size * 0.58)
        if lh * len(lines) <= qh:
            best_lines, best_font = lines, fnt
            break
        fit = max(1, qh // max(1, lh))
        clipped_lines = wrap_text(draw, f"「{qraw}。」", fnt, qw - 56, max_lines=fit)
        if clipped_lines:
            best_lines, best_font, clipped = clipped_lines, fnt, True
    if clipped:
        best_lines = add_ellipsis(draw, best_lines, best_font, qw - 56)
    qyy = qy
    qlh = text_height(draw, best_font) + int(best_font.size * 0.68)
    quote_top = qyy
    for ln in best_lines:
        draw.text((qx + 34, qyy), ln, font=best_font, fill="#3a322b")
        qyy += qlh
    quote_bottom = qyy - int(best_font.size * 0.22)
    draw.rectangle((qx, quote_top, qx + 6, quote_bottom), fill=col)

    no_font = sans(52)
    wm_font = sans(42)
    no_text = f"No.{n:03d}"
    wm_text = "用 Corvio 整理 · corvio.ai"
    right_margin = 240
    no_w = draw.textlength(no_text, font=no_font)
    wm_w = draw.textlength(wm_text, font=wm_font)
    right_block_w = max(no_w, wm_w)
    right_block_x = int(W - right_margin - right_block_w)
    meta_gap = 72
    meta_limit = max(220, right_block_x - meta_gap - rx)
    if sp and draw.textlength(meta, font=sans(54)) > meta_limit:
        meta = meta.replace(f" · {sp}", f"\n{sp}")
    meta_lines, fitted_meta_font, _ = fit_text_block(draw, meta, meta_limit, sans, (54, 50, 46, 42), 2)
    no_x = int(W - right_margin - no_w)
    wm_x = int(W - right_margin - wm_w)
    no_y = H - 312
    wm_y = no_y + text_height(draw, no_font) + 18
    meta_lh = text_height(draw, fitted_meta_font) + int(fitted_meta_font.size * 0.45)
    meta_h = meta_lh * len(meta_lines) - int(fitted_meta_font.size * 0.45)
    meta_y0 = wm_y + text_height(draw, wm_font) - meta_h
    for i, line in enumerate(meta_lines):
        draw.text((rx, meta_y0 + i * meta_lh), line, font=fitted_meta_font, fill=MUTED)
    draw.text((no_x, no_y), no_text, font=no_font, fill=MUTED)
    draw.text((wm_x, wm_y), wm_text, font=wm_font, fill="#9a9894")
    img.save(path, "JPEG", quality=88)


def render_with_playwright(sync_playwright, jobs, img_dir, Image):
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for name, html, w, h in jobs:
            hp = os.path.join(img_dir, name + ".html")
            with open(hp, "w", encoding="utf-8") as f:
                f.write(html)
            pg = br.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
            pg.goto("file://" + hp)
            pg.wait_for_timeout(350)
            png = os.path.join(img_dir, name + ".png")
            jpg = os.path.join(img_dir, name + ".jpg")
            pg.screenshot(path=png)
            pg.close()
            Image.open(png).convert("RGB").save(jpg, quality=88)
            os.remove(png)
            os.remove(hp)
            print(jpg, os.path.getsize(jpg))
        br.close()


def render_with_pil(books, f, sel, cur, by, img_dir, cov_dir, name, fonts, Image, ImageColor, ImageDraw, ImageFont, ImageOps):
    cover_path = os.path.join(img_dir, "library-cover.jpg")
    render_cover_pil(cover_path, books, f, name, fonts, Image, ImageColor, ImageDraw, ImageFont)
    print(cover_path, os.path.getsize(cover_path))
    for s in sel:
        b = by[s["id"]]
        cp = ensure_cover(b, cov_dir, Image)
        out = os.path.join(img_dir, f'exlibris-{s["no"]:03d}.jpg')
        render_plate_pil(out, b, cur[s["id"]], s["no"], cp, name, fonts, Image, ImageDraw, ImageFont, ImageOps)
        print(out, os.path.getsize(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--name", default="我的图书馆")
    ap.add_argument("--include-private", action="store_true")
    ap.add_argument("--engine", choices=("auto", "playwright", "pil"), default="auto")
    ap.add_argument("--font", help="手动指定中文字体文件路径（ttf/ttc/otf）")
    a = ap.parse_args()
    a.data, a.work = os.path.abspath(a.data), os.path.abspath(a.work)
    engine, sync_playwright = choose_engine(a.engine)
    Image, ImageColor, ImageDraw, ImageFont, ImageOps = load_pillow()
    fonts = choose_font(ImageFont, a.font)

    books, by, f, sel, cur = read_jobs(a.data, a.work, a.include_private)
    img_dir = os.path.join(a.work, "images")
    cov_dir = os.path.join(a.work, "covers")
    os.makedirs(img_dir, exist_ok=True)
    os.makedirs(cov_dir, exist_ok=True)

    if engine == "playwright":
        jobs = [("library-cover", cover_html(books, f, a.name), 1600, 900)]
        for s in sel:
            b = by[s["id"]]
            cp = ensure_cover(b, cov_dir, Image)
            jobs.append((f'exlibris-{s["no"]:03d}', plate_html(b, cur[s["id"]], s["no"], cp, a.name), 1600, 640))
        render_with_playwright(sync_playwright, jobs, img_dir, Image)
        return
    render_with_pil(books, f, sel, cur, by, img_dir, cov_dir, a.name, fonts, Image, ImageColor, ImageDraw, ImageFont, ImageOps)


if __name__ == "__main__":
    main()
