#!/usr/bin/env python3
"""图书馆的数据层：把 fetch.py 拉下来的原始 JSON 整理成统一结构，并提供给 agent 用的子命令。

子命令：
  select   挑出要上架的书，写入 <work>/selection.json（已有书目时用 --add / --add-books / --reset）
  digest   打印选中书目的阅读摘要，供 agent 写策展内容（--only-new 只打还没有条目的书）
  check    核对 agent 写的 curation.json：引文必须逐字来自用户的划线或想法，数字和年份要能在数据里找到
  facts    打印全馆统计（总览页用），也写入 <work>/facts.json

示例：
  python3 library.py select --data ./weread-data --work ./work --top 10
  python3 library.py select --data ./weread-data --work ./work --add 5
  python3 library.py digest --data ./weread-data --work ./work --only-new
  python3 library.py check  --data ./weread-data --work ./work
"""
import argparse, collections, datetime as dt, glob, json, os, re, sys

PALETTE = {"经济理财": "#7a3b2e", "个人成长": "#8a6a2f", "文学": "#4f6346", "精品小说": "#3e5670",
           "心理": "#2f5552", "社会文化": "#6a4058", "历史": "#6b5236", "哲学宗教": "#4a4a6a",
           "科学技术": "#2f4a63", "政治军事": "#5a4a3a", "生活百科": "#5b6b5a", "人物传记": "#5d5048",
           "艺术": "#6b4a3a", "教育学习": "#3f5f4f", "其他": "#5a5550"}


def color(top):
    return PALETTE.get(top, PALETTE["其他"])


def ts(t):
    return dt.datetime.fromtimestamp(t, dt.timezone(dt.timedelta(hours=8)))


def short_title(t):
    return re.split(r"[：:（(]", t or "")[0].strip() or (t or "")


def load(data, include_private=False):
    """返回 (shelf, books)。books 只包含有笔记的书，按统一结构整理。"""
    shelf = json.load(open(os.path.join(data, "shelf.json"))).get("books") or []
    private = {str(b["bookId"]) for b in shelf if b.get("secret")}
    shelf_by = {str(b["bookId"]): b for b in shelf}
    nb = {str(b["bookId"]): b for b in json.load(open(os.path.join(data, "notebooks.json")))["books"]}
    books = []
    for f in glob.glob(os.path.join(data, "books", "*.json")):
        d = json.load(open(f))
        bid = str(d["bookId"])
        if bid in private and not include_private:
            continue
        info = d.get("info") or {}
        if "_error" in info:
            info = {}
        sb = shelf_by.get(bid, {})
        chap = {}
        for c in ((d.get("chapters") or {}).get("chapters") or []):
            chap[c.get("chapterUid")] = c
        for c in ((d.get("bookmarks") or {}).get("chapters") or []):
            chap.setdefault(c.get("chapterUid"), c)
        items = []
        for m in (d.get("bookmarks") or {}).get("updated") or []:
            items.append({"kind": "划线", "id": m.get("bookmarkId"), "text": (m.get("markText") or "").strip(),
                          "chapterUid": m.get("chapterUid"), "time": m.get("createTime"), "range": m.get("range")})
        for r in d.get("reviews") or []:
            rv = r.get("review") or r
            items.append({"kind": "想法", "id": r.get("reviewId") or rv.get("reviewId"), "text": (rv.get("content") or "").strip(),
                          "abstract": (rv.get("abstract") or "").strip(), "chapterUid": rv.get("chapterUid"),
                          "time": rv.get("createTime"), "range": rv.get("range")})
        items = [i for i in items if i["text"] and i["text"] != "[插图]"]
        if not items:
            continue
        for i in items:
            c = chap.get(i["chapterUid"]) or {}
            i["chapter"] = c.get("title") or "未标章节"
            i["chapterIdx"] = c.get("chapterIdx", 9999)
            try:
                i["offset"] = int(str(i.get("range") or "0-0").split("-")[0])
            except ValueError:
                i["offset"] = 0
        prog = (d.get("progress") or {}).get("book") or {}
        cat = info.get("category") or "其他"
        n = nb.get(bid, {})
        books.append({
            "id": bid, "title": info.get("title") or sb.get("title") or "未知书名",
            "author": info.get("author") or sb.get("author") or "", "translator": info.get("translator") or "",
            "cover": info.get("cover") or sb.get("cover"), "intro": info.get("intro") or "",
            "category": cat, "top": cat.split("-")[0], "publisher": info.get("publisher") or "",
            "year": (info.get("publishTime") or "")[:4], "rating": (info.get("newRating") or 0) / 10,
            "ratingCount": info.get("newRatingCount") or 0, "deepLink": info.get("deepLink") or sb.get("deepLink"),
            "progress": n.get("readingProgress", prog.get("progress")) or 0, "readingTime": prog.get("readingTime") or 0,
            "last": prog.get("updateTime") or sb.get("readUpdateTime"), "finished": bool(sb.get("finishReading")),
            "items": items, "nh": sum(1 for i in items if i["kind"] == "划线"), "nr": sum(1 for i in items if i["kind"] == "想法")})
    return shelf, books


def status(p, finished=False):
    if finished or (p or 0) >= 98:
        return "读完"
    return "读到一半" if (p or 0) >= 20 else "刚翻开"


def span(b):
    t = [i["time"] for i in b["items"] if i["time"]]
    return (min(t), max(t)) if t else (None, None)


def facts(data, include_private=False):
    shelf, books = load(data, include_private)
    ro = json.load(open(os.path.join(data, "readdata_overall.json")))
    st_path = os.path.join(data, "fetch_state.json")
    partial = False
    if os.path.exists(st_path):
        try:
            st = json.load(open(st_path, encoding="utf-8"))
            partial = bool(st.get("partial")) if isinstance(st, dict) else False
        except Exception:
            partial = False
    shown = [b for b in shelf if include_private or not b.get("secret")]
    prefer_category = ro.get("preferCategory") or []
    if partial:
        private = {str(b["bookId"]) for b in shelf if b.get("secret")}
        nb = json.load(open(os.path.join(data, "notebooks.json"), encoding="utf-8"))["books"]
        chosen = [b for b in nb if b.get("bookId") and (include_private or str(b["bookId"]) not in private)]
        noted = len(chosen)
        # notebooks 里 bookmarkCount 基本是 0，划线数在 noteCount
        highlights = sum(b.get("noteCount") or 0 for b in chosen)
        thoughts = sum(b.get("reviewCount") or 0 for b in chosen)
        # notebooks 的 sort 是每本书最后一次记笔记的时间，推不出最早的笔记，开馆时间等全量数据再写
        first, years, peak = None, {}, None
    else:
        t = [i["time"] for b in books for i in b["items"] if i["time"]]
        year_c = collections.Counter(ts(x).year for x in t)
        noted, highlights, thoughts = len(books), sum(b["nh"] for b in books), sum(b["nr"] for b in books)
        first, years = (min(t) if t else None), dict(sorted(year_c.items()))
        peak = year_c.most_common(1)[0] if year_c else None
    return {
        "shelf": len(shown), "noted": noted, "highlights": highlights, "thoughts": thoughts,
        "hours": round((ro.get("totalReadTime") or 0) / 3600), "readDays": ro.get("readDays"),
        "finished": sum(1 for b in shown if b.get("finishReading")), "first": first, "years": years,
        "peakYear": peak, "preferCategory": prefer_category, "readLongest": ro.get("readLongest") or [], "partial": partial}


def pick_ids(a, books, by):
    if a.books:
        return [x for x in a.books.split(",") if x in by]
    if a.recent:
        return [b["id"] for b in sorted(books, key=lambda b: -(span(b)[1] or 0))[:a.recent]]
    return [b["id"] for b in sorted(books, key=lambda b: -len(b["items"]))[:a.top]]


def cmd_select(a):
    _, books = load(a.data, a.include_private)
    by = {b["id"]: b for b in books}
    os.makedirs(a.work, exist_ok=True)
    sel_path = os.path.join(a.work, "selection.json")
    existing = []
    if os.path.exists(sel_path) and os.path.getsize(sel_path):
        existing = json.load(open(sel_path, encoding="utf-8")) or []
    append = bool(a.add_books or a.add is not None) and not a.reset
    if existing and not append and not a.reset:
        sys.exit("selection.json 已存在。请用 --add N（再追加 N 本）、--add-books id1,id2 或 --reset（清空重选）。")
    if append:
        base = list(existing)
        have = {s["id"] for s in base}
        for s in base:
            if s["id"] not in by:
                print(f'警告：No.{s["no"]:03d}《{s.get("title") or s["id"]}》在这次的数据里找不到，仍保留在 selection.json', file=sys.stderr)
        if a.add_books:
            ids = []
            for x in a.add_books.split(","):
                if x and x in by and x not in have and x not in ids:
                    ids.append(x)
        else:
            ranked = [b["id"] for b in sorted(books, key=lambda b: -len(b["items"])) if b["id"] not in have]
            ids = ranked[:a.add]
        start = max((s.get("no") or 0 for s in base), default=0) + 1
    else:
        base, ids, start = [], pick_ids(a, books, by), 1
    added = [{"no": start + i, "id": bid, "title": by[bid]["title"]} for i, bid in enumerate(ids)]
    sel = base + added
    json.dump(sel, open(sel_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    new_ids = {s["id"] for s in added}
    for s in sel:
        mark = "+" if s["id"] in new_ids else " "
        print(f'{mark} No.{s["no"]:03d}  {s["id"]}  《{s["title"]}》')


def cmd_digest(a):
    _, books = load(a.data, a.include_private)
    by = {b["id"]: b for b in books}
    sel = json.load(open(os.path.join(a.work, "selection.json"), encoding="utf-8"))
    cur = {}
    cur_path = os.path.join(a.work, "curation.json")
    if a.only_new and os.path.exists(cur_path):
        cur = json.load(open(cur_path, encoding="utf-8")) or {}
    for s in sel:
        if a.only_new and s["id"] in cur:
            continue
        b = by.get(s["id"])
        if not b:
            print(f'警告：No.{s["no"]:03d}《{s.get("title") or s["id"]}》在数据里找不到，已跳过', file=sys.stderr)
            continue
        f0, f1 = span(b)
        top_ch = collections.Counter(i["chapter"] for i in b["items"]).most_common(2)
        print(f'\n## {b["id"]}《{b["title"]}》 作者:{b["author"]} | 分类:{b["category"]} | {b["publisher"]} {b["year"]}'
              f' | 推荐值:{b["rating"]:.1f}%({b["ratingCount"]}人) | 进度:{b["progress"]}% | 阅读时长:{b["readingTime"]/3600:.1f}h'
              f' | 划线{b["nh"]} 想法{b["nr"]} | 笔记时间:{ts(f0).date() if f0 else "-"} ~ {ts(f1).date() if f1 else "-"}'
              f' | 笔记最多的章节:{top_ch}')
        for i in sorted(b["items"], key=lambda x: (x["chapterIdx"], x["offset"])):
            if i["kind"] == "想法":
                print(f'  [想法|{i["chapter"][:16]}|{ts(i["time"]).date() if i["time"] else ""}] {i["text"][:120]}'
                      + (f'  ⟵ 原文:{i["abstract"][:60]}' if i.get("abstract") else ""))
            elif len(i["text"]) <= a.max_len:
                print(f'  [划线|{i["chapter"][:16]}|{ts(i["time"]).date() if i["time"] else ""}] {i["text"]}')


def norm(s):
    return re.sub(r"[\s「」『』“”\"'。，,.!！?？…]", "", s or "")


def about_quotes(text):
    if not isinstance(text, str) or not text:
        return []
    found = []
    for m in re.finditer(r"“([^”]+)”|「([^」]+)」|『([^』]+)』", text):
        q = m.group(1) or m.group(2) or m.group(3) or ""
        q = q.strip()
        if q:
            found.append(q)
    return found


def allowed_nums(b):
    p = b["progress"] or 0
    hours = (b["readingTime"] or 0) / 3600
    nums = {str(b["nh"]), str(b["nr"]), str(b["nh"] + b["nr"]), str(len(b["items"])),
            str(p), str(round(p)), f"{p:.0f}", f"{p:.1f}",
            f"{hours:.0f}", f"{hours:.1f}", str(round(hours)), str(int(hours)),
            f'{b["rating"]:.1f}', str(b["ratingCount"]), str(b["year"] or "")}
    rc = b["ratingCount"] or 0
    nums |= {str(rc // 10**k * 10**k) for k in range(1, 6)} | {str(rc // 10**k) for k in range(1, 6)}
    ch = collections.Counter(i["chapter"] for i in b["items"])
    nums |= {str(n) for _, n in ch.most_common(3)}
    nums.add(str(len(ch)))
    nums.add(str(len({i["chapterUid"] for i in b["items"]})))
    f0, f1 = span(b)
    if f0:
        d0, d1 = ts(f0), ts(f1)
        nums |= {str(d0.month), str(d0.day), str(d1.month), str(d1.day)}
        days = (d1.date() - d0.date()).days
        months = (d1.year - d0.year) * 12 + (d1.month - d0.month)
        years = {ts(i["time"]).year for i in b["items"] if i.get("time")}
        nums |= {str(days), str(days + 1), str(months), str(months + 1),
                 str(len(years)), str(d1.year - d0.year + 1)}
    return nums


def cmd_check(a):
    _, books = load(a.data, a.include_private)
    by = {b["id"]: b for b in books}
    sel = json.load(open(os.path.join(a.work, "selection.json"), encoding="utf-8"))
    cur = json.load(open(os.path.join(a.work, "curation.json"), encoding="utf-8"))
    sel_ids = {s["id"] for s in sel}
    problems = 0
    for s in sel:
        b = by.get(s["id"])
        title = (b or {}).get("title") or s.get("title") or s["id"]
        if not b:
            print(f'✗ 《{title}》在数据里找不到'); problems += 1; continue
        c = cur.get(s["id"])
        if not isinstance(c, dict):
            print(f'✗ 《{title}》缺少策展内容'); problems += 1; continue
        if "author" in c:
            print(f'✗ 《{title}》作者只能来自微信读书数据，请删掉 author 字段'); problems += 1
        raw_q = c.get("quote")
        raw_q = raw_q if isinstance(raw_q, str) else ""
        q = norm(raw_q)
        corpus = [norm(i["text"]) for i in b["items"]] + [norm(i.get("abstract", "")) for i in b["items"]]
        if len(q) < 6:
            print(f'✗ 《{title}》quote 太短，像是截出来的片段'); problems += 1
        if not any(q in x for x in corpus if x):
            print(f'✗ 《{title}》quote 不是逐字摘自用户的划线或想法：{c.get("quote")}'); problems += 1
        if "short" in c:
            raw_s = c.get("short")
            if not isinstance(raw_s, str) or norm(raw_s) not in norm(b["title"]):
                print(f'✗ 《{title}》short 不是书名的一部分'); problems += 1
        tags = c.get("tags") or []
        if not isinstance(tags, list) or len(tags) > 3 or any(not isinstance(t, str) or not 0 < len(norm(t)) <= 6 for t in tags):
            print(f'✗ 《{title}》关键词最多 3 个，每个 1–6 个字'); problems += 1
        about = c.get("about") or ""
        corpus = [norm(i["text"]) for i in b["items"]] + [norm(i.get("abstract", "")) for i in b["items"]]
        chapter_norms = {norm(i["chapter"]) for i in b["items"] if i.get("chapter")}
        for quoted in about_quotes(about):
            if len(quoted) < 4:
                continue
            nq = norm(quoted)
            if not nq:
                continue
            if nq in chapter_norms:
                continue
            if not any(nq in x for x in corpus if x):
                print(f'✗ 《{title}》about 里引用的『{quoted}』不是用户的原话'); problems += 1
        f0, f1 = span(b)
        allowed_years = set(range(ts(f0).year, ts(f1).year + 1)) if f0 else set()
        for y in re.findall(r"(20\d\d|19\d\d)", about):
            if int(y) not in allowed_years and y != b["year"]:
                print(f'✗ 《{title}》about 里的年份 {y} 不在笔记时间范围 {sorted(allowed_years)} 内'); problems += 1
        nums = allowed_nums(b)
        for n in re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", re.sub(r"(20\d\d|19\d\d)", "", about)):
            in_chapter = any(n in re.findall(r"\d+", m) for m in re.findall(r"第[\d、，,\s和与至到-]+[章节部篇]", about))
            if n not in nums and not in_chapter:
                print(f'✗ 《{title}》about 里的数字 {n} 在数据里找不到，请删掉或改写这句话'); problems += 1
        for r in c.get("related") or []:
            if r not in sel_ids or r == s["id"]:
                print(f'✗ 《{title}》related 里的 {r} 不是本次上架的其他书'); problems += 1
    print("核对完成：" + ("全部通过" if problems == 0 else f"{problems} 处必须修改"))
    sys.exit(1 if problems else 0)


def cmd_facts(a):
    f = facts(a.data, a.include_private)
    os.makedirs(a.work, exist_ok=True)
    json.dump(f, open(os.path.join(a.work, "facts.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(f, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("select", "digest", "check", "facts"):
        p = sp.add_parser(name)
        p.add_argument("--data", required=True)
        p.add_argument("--work", required=True)
        p.add_argument("--include-private", action="store_true", help="包含私密书架上的书（默认不包含）")
        if name == "select":
            p.add_argument("--top", type=int, default=10)
            p.add_argument("--recent", type=int)
            p.add_argument("--books", help="逗号分隔的 bookId")
            p.add_argument("--add", type=int, metavar="N", help="在现有 selection 之外再按笔记数追加 N 本")
            p.add_argument("--add-books", help="逗号分隔的 bookId，追加到现有 selection")
            p.add_argument("--reset", action="store_true", help="忽略已有 selection，按 --top / --recent / --books 重选")
        if name == "digest":
            p.add_argument("--max-len", type=int, default=80, help="只打印不超过这个长度的划线，想法全部打印")
            p.add_argument("--only-new", action="store_true", help="只输出 curation.json 里还没有条目的书")
    a = ap.parse_args()
    {"select": cmd_select, "digest": cmd_digest, "check": cmd_check, "facts": cmd_facts}[a.cmd](a)
