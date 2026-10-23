#!/usr/bin/env python3
"""生成图书馆的 Corvio 页面（Markdown）。

平时由 publish.py 调用，不需要单独运行。只接了 Corvio MCP、没有 corvio CLI 时，才手动用：
  python3 pages.py books    --data D --work W    输出 <work>/md/001.md ...
  python3 pages.py overview --data D --work W    输出 <work>/md/overview.md

图片、链接都取自 <work>/state.json（格式见 publish.py），缺省的就不放。
"""
import argparse, collections, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from library import load, facts, short_title, status, ts

ZCOLOR = {"经济理财": "red", "个人成长": "yellow", "心理": "green", "科学技术": "blue", "文学": "green",
          "精品小说": "blue", "社会文化": "purple", "历史": "brown", "哲学宗教": "purple", "人物传记": "brown"}
SCOLOR = {"读完": "green", "读到一半": "orange", "刚翻开": "gray"}


def rd(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def img(alt, aid):
    return f"![{alt}](</api/v1/workspace-assets/{aid}/content>)" if aid else ""


def dshort(t):
    x = ts(t); return f"{x.year}.{x.month:02d}.{x.day:02d}"


def one(s):
    return " ".join((s or "").split()).replace("|", "｜")


def quoted(s):
    return f"「{one(s).rstrip('。')}。」"


def gray(t):
    return f'<span style="color:#9b9b9b">{t}</span>'


def thought_md(i):
    """Corvio 的引用块一行一块、相邻的会连成一片，所以引用块里只放原文，你写的话和日期跟在下面一行。"""
    meta = f"　{gray(dshort(i['time']))}" if i.get("time") else ""
    return ([f"> {one(i['abstract'])}", ""] if i.get("abstract") else []) + \
        [f'**<span style="color:#9a6b3f">你写道</span>**：{one(i["text"])}{meta}', ""]


def highlight_md(i, best=False):
    """best：这本书「最舍不得的一句」所在的划线，用高亮标出来。"""
    t = one(i["text"])
    return [f"> {f'=={t}==' if best else t}" + (f"　{gray(dshort(i['time']))}" if i.get("time") else ""), ""]


def card_author(s):
    return re.sub(r"(?<=[\u4e00-\u9fff·])\s+(?=[\u4e00-\u9fff·])", "、", one(s or ""))


def card_rows(b):
    """卡片里随阅读变化的行，同步时按行首替换。"""
    return {
        "阅读": f"| 阅读 | {status(b['progress'], b['finished'])} · {b['readingTime']/3600:.1f} 小时 |",
        "笔记": f"| 笔记 | {b['nh']} 条划线 · {b['nr']} 条想法 |",
    }


def by_chapter(items):
    groups = collections.OrderedDict()
    for i in sorted(items, key=lambda x: (x["chapterIdx"], x["offset"])):
        groups.setdefault(i["chapter"], []).append(i)
    return groups


def note_md(items, best=None, quoted=None):
    """一章的笔记，按书里的先后排；想法已经引了原文的划线（quoted）不再单独列出。"""
    if quoted is None:
        quoted = {one(i["abstract"]) for i in items if i["kind"] == "想法" and i.get("abstract")}
    return sum((thought_md(i) if i["kind"] == "想法" else highlight_md(i, i["id"] == best)
                for i in items if i["kind"] == "想法" or one(i["text"]) not in quoted), [])


def book_page(b, c, n, assets, related=None):
    title = c.get("short") or short_title(b["title"])
    L = []
    if assets.get(f"{n:03d}"):
        L += [img(f"藏书票 · {title}", assets[f"{n:03d}"]), ""]
    else:
        L += [f"> {quoted(c['quote'])}", "", "*—— 你在这本书里最舍不得的一句*", ""]
    # 表头不能留空，Corvio 不认空表头的表格，所以拿第一行当表头
    L += ["## 这本书", "", f"| 作者 | {card_author(b['author']) or '—'} |", "| --- | --- |", f"| 分区 | {b['category'].replace('-', ' · ')} |"]
    if c.get("tags"):
        L.append(f"| 关键词 | {' · '.join(c['tags'][:3])} |")
    L += list(card_rows(b).values())
    L += ["", "## 这本书和你", "", c["about"].strip(), ""]
    if related:  # [(书名, url 或 None)]
        L += ["**相关馆藏**：" + "、".join(f"[《{t}》]({u})" if u else f"《{t}》" for t, u in related), ""]
    L += ["*由 AI 根据你的笔记整理*", ""]
    L += ["## 我的笔记", ""]
    q = one(c["quote"]).rstrip("。")
    best = next((i["id"] for i in b["items"] if i["kind"] == "划线" and q in one(i["text"])), None)
    for ch, items in by_chapter(b["items"]).items():
        L += [f"### {ch}", ""] + note_md(items, best)
    return "\n".join(L)


def book_url(st, bid):
    return ((st.get("books") or {}).get(bid) or {}).get("url")


def shelf_table(sel, by, cur, st):
    zones = []
    for s in sel:
        z = by[s["id"]]["top"]
        if z not in zones:
            zones.append(z)
    zid = {z: f"Z{k}" for k, z in enumerate(zones)}
    sid = {s: f"S{k}" for k, s in enumerate(SCOLOR)}
    y = ["```corvio-table", "schema_version: 1", "table_id: T_shelf", "layout:", "  sticky_header: true", "  border_style: minimal", "columns:",
         "  - column_id: C_book", "    header_text: 书", "    width_px: 170",
         "  - column_id: C_quote", "    header_text: 你最舍不得的一句", "    width_px: 260",
         "  - column_id: C_zone", "    header_text: 分区", "    width_px: 90", "    select:", "      mode: single", "      options:"]
    for z in zones:
        y += [f"        - option_id: {zid[z]}", f"          label: {z}", f"          color: {ZCOLOR.get(z, 'gray')}"]
    y += ["  - column_id: C_state", "    header_text: 状态", "    width_px: 70", "    select:", "      mode: single", "      options:"]
    for s_, c_ in SCOLOR.items():
        y += [f"        - option_id: {sid[s_]}", f"          label: {s_}", f"          color: {c_}"]
    y += ["rows:",
          "  - row_id: R_h", "    kind: header", "    cells:"]
    for cid, t in [("C_book", "书"), ("C_quote", "你最舍不得的一句"), ("C_zone", "分区"), ("C_state", "状态")]:
        y += [f"      - cell_id: K_h_{cid}", f"        column_id: {cid}", "        content: |", f"          {t}"]
    for s in sel:
        b = by[s["id"]]; c = cur[s["id"]]; n = s["no"]; z = b["top"]
        stt = status(b["progress"], b["finished"])
        name = c.get("short") or short_title(b["title"])
        link = f"[《{name}》]({book_url(st, b['id'])})" if book_url(st, b["id"]) else f"《{name}》"
        y += [f"  - row_id: R_{n}", "    kind: body", "    cells:",
              f"      - cell_id: K_{n}_book", "        column_id: C_book", "        content: |", f"          {link}",
              f"      - cell_id: K_{n}_quote", "        column_id: C_quote", "        content: |", f"          {quoted(c['quote'])}",
              f"      - cell_id: K_{n}_zone", "        column_id: C_zone", "        selected_option_ids:", f"          - {zid[z]}", "        content: |", f"          {z}",
              f"      - cell_id: K_{n}_state", "        column_id: C_state", "        selected_option_ids:", f"          - {sid[stt]}", "        content: |", f"          {stt}"]
    return y + ["```"]


def overview(data, include_private, sel, by, books, cur, st, name="我的图书馆"):
    f = facts(data, include_private)
    sel = [s for s in sel if s["id"] in by and s["id"] in cur]
    L = []
    if (st.get("cover") or {}).get("asset_id"):
        L += [img(name, st["cover"]["asset_id"]), ""]
    fy = ts(f["first"]) if f["first"] else None
    cats = list(dict.fromkeys(c["parentCategoryTitle"] for c in sorted(f["preferCategory"], key=lambda c: -(c.get("readingTime") or 0))
                              if c.get("parentCategoryTitle")))[:2]
    like = f"你读得最多的是{'和'.join(cats)}。" if cats else ""
    if fy:
        L += [f"这座图书馆从 {fy.year} 年 {fy.month} 月开始收书" + (f"，{like}" if like else "。"), ""]
    elif like:
        L += [like, ""]
    L += ["## 我的阅读", "", "| 藏书 | 留下笔记的书 | 划线 | 想法 | 阅读时长 |", "| --- | --- | --- | --- | --- |",
          f"| {f['shelf']} 本 | {f['noted']} 本 | {f['highlights']} 条 | {f['thoughts']} 条 | {f['hours']} 小时 |", ""]
    L += ["## 书架", "", "点书名翻开这本书。", ""] + shelf_table(sel, by, cur, st) + [""]
    rest = f["noted"] - len(sel)
    secret = {str(b["bookId"]) for b in rd(os.path.join(data, "shelf.json"), {}).get("books", []) if b.get("secret")}
    longest = [x for x in f["readLongest"] if x.get("readTime") and (include_private or str(x["book"]["bookId"]) not in secret)]
    notes = []
    if longest:
        notes += ["## 读得最久的书", "", "| 书 | 阅读时长 |", "| --- | --- |"]
        for x in sorted(longest, key=lambda x: -x["readTime"])[:3]:
            t, u = short_title(x["book"]["title"]), book_url(st, str(x["book"]["bookId"]))
            notes.append(f"| {f'[《{t}》]({u})' if u else f'《{t}》'} | {x['readTime']/3600:.1f} 小时 |")
        notes += [""]
    L += notes
    ex = (cur[sel[0]["id"]].get("short") or short_title(by[sel[0]["id"]]["title"])) if sel else "某本书"
    L += ["## 怎么用这座图书馆", "", "接了 Corvio 的 AI 都能读这座图书馆，试试说：", "",
          "- 「我在哪本书里读到过关于『选择』的话？」",
          f"- 「采访一下我读的《{ex}》，把重点记到这本书的页面上。」",
          "- 「根据我的图书馆，下一本我该读什么？」", ""]
    if rest > 0:
        L += [f"*还有 {rest} 本留有笔记的书，对 AI 说「把剩下的书也整理进{name}」*"]
    else:
        L += ["*由 weread-library 整理*"]
    return "\n".join(L)


def load_state(work):
    return rd(os.path.join(work, "state.json"), {"books": {}})


def save_state(work, st):
    p = os.path.join(work, "state.json")
    json.dump(st, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)


def context(data, work, include_private):
    """publish.py 和本脚本共用：读取数据、选书、策展和 state。"""
    shelf, books = load(data, include_private)
    by = {b["id"]: b for b in books}
    sel = json.load(open(os.path.join(work, "selection.json")))
    cur = json.load(open(os.path.join(work, "curation.json"), encoding="utf-8"))
    missing = [s for s in sel if s["id"] not in by or s["id"] not in cur]
    for s in missing:
        print(f'跳过 No.{s["no"]:03d}《{s["title"]}》：' + ("数据里找不到" if s["id"] not in by else "curation.json 里没有它"), file=sys.stderr)
    sel = [s for s in sel if s not in missing]
    return books, by, sel, cur, load_state(work)


def related_of(bid, by, cur, st):
    return [(cur[r].get("short") or short_title(by[r]["title"]), book_url(st, r))
            for r in cur[bid].get("related", []) if r in cur and r in by and r != bid]


def render_book(s, by, cur, st):
    bid = s["id"]
    return book_page(by[bid], cur[bid], s["no"], {f'{s["no"]:03d}': ((st["books"].get(bid) or {}).get("asset_id"))},
                     related_of(bid, by, cur, st))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["books", "overview"])
    ap.add_argument("--data", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--name", help="图书馆名，默认用 state.json 里的，没有就是「我的图书馆」")
    ap.add_argument("--include-private", action="store_true")
    a = ap.parse_args()
    books, by, sel, cur, st = context(a.data, a.work, a.include_private)
    name = a.name or st.get("name") or "我的图书馆"
    md = os.path.join(a.work, "md"); os.makedirs(md, exist_ok=True)
    if a.what == "books":
        for s in sel:
            p = os.path.join(md, f'{s["no"]:03d}.md')
            open(p, "w", encoding="utf-8").write(render_book(s, by, cur, st))
            print(p, os.path.getsize(p))
    else:
        p = os.path.join(md, "overview.md")
        open(p, "w", encoding="utf-8").write(overview(a.data, a.include_private, sel, by, books, cur, st, name))
        print(p, os.path.getsize(p))


if __name__ == "__main__":
    main()
