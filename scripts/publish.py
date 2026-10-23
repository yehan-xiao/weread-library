#!/usr/bin/env python3
"""把图书馆写进 Corvio（通过官方 corvio CLI）。首次搭建、追加书、同步新划线都是这一条命令，可以重复运行。

用法：
  python3 publish.py --data D --work W --workspace <工作区 id> [--name "我的图书馆"]   # 第一次
  python3 publish.py --data D --work W                                                # 之后（工作区记在 state.json 里）
  加 --dry-run 只打印要做什么，不写入。

每次运行会：
  1. 上传新的或有变化的图片（<work>/images 下的封面和藏书票）；
  2. 没有总览页就建一个占位页；
  3. 给还没有页面的书建页；已有页面的书，只把新划线、新想法插进「我的笔记」对应章节，并刷新卡片里的阅读与笔记，
     用户在页面上自己写的内容原样保留；
  4. 重新生成总览页并整页更新（如果发现总览页被人改过，默认跳过，加 --force-overview 才覆盖）。

进度都记在 <work>/state.json，中途断了重跑即可：
  {"workspace": "...", "name": "我的图书馆", "home": {"id", "node_id", "url", "revision", "sha"},
   "cover": {"asset_id", "sha"},
   "books": {"<bookId>": {"no", "id", "node_id", "url", "revision", "asset_id", "plate_sha", "card", "written": [划线/想法 id]}}}

只接了 Corvio MCP、没有 CLI 时，用 `publish.py record` 把 MCP 返回的结果记进 state.json，见 SKILL.md。
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from library import facts, short_title
from pages import (by_chapter, card_rows, context, note_md, overview, render_book, save_state, one)

APP = "https://corvio.ai/app/d/"
LOCK = threading.Lock()


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


class Corvio:
    def __init__(self, ws):
        self.ws = ws

    def __call__(self, *args):
        cmd = ["corvio", "--workspace", self.ws, "--json", "--no-input", *args]
        for k in range(6):
            p = subprocess.run(cmd, capture_output=True, text=True)
            try:
                r = json.loads(p.stdout)
            except ValueError:
                r = {"error": {"code": "bad_output", "message": (p.stderr or p.stdout)[-300:]}}
            e = r.get("error") if isinstance(r, dict) else None
            if not e:
                return r
            if not (e.get("details") or {}).get("safe_to_retry") and e.get("code") not in ("network_error", "bad_output"):
                raise RuntimeError(f"corvio {args[0]} {args[1]} 失败：{e.get('code')} {e.get('message')}")
            limited = "rate_limit" in json.dumps(e)  # 上传有频率限制，批量上架时会碰到，要等久一点
            time.sleep((20 if limited else 3) * (k + 1))
        raise RuntimeError(f"corvio {args[0]} {args[1]} 多次重试仍失败：{e.get('code')} {e.get('message')}")

    def upload(self, path):
        return self("files", "upload", "--file", path, "--mime-type", "image/jpeg")["asset_id"]

    def create(self, title, md, parent=None, key=None):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(md)
        args = ["docs", "create", "--title", title, "--file", f.name] + (["--parent-node-id", parent] if parent else []) + \
               (["--idempotency-key", key] if key else [])
        try:
            r = self(*args)
        finally:
            os.remove(f.name)
        return {"id": r["id"], "node_id": r["node_id"], "url": APP + r["id"], "revision": r["content_revision"]}

    def get(self, doc_id):
        r = self("docs", "get", doc_id)
        if r.get("content_complete") is False:
            raise RuntimeError(f"读取页面 {doc_id} 不完整")
        return r["markdown"], r["content_revision"]

    def update(self, doc_id, md, rev, summary):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(md)
        try:
            r = self("docs", "update", doc_id, "--file", f.name, "--expected-revision", str(rev), "--change-summary", summary)
        finally:
            os.remove(f.name)
        return r["content_revision"]


# ---------- 同步：把新内容插进已有书页，不动用户写的东西 ----------

def _sec(L, name, start=0):
    for k in range(start, len(L)):
        if L[k].strip() == f"## {name}":
            return k


def _end(L, k):
    """第 k 行这个二级标题的小节在哪一行结束（下一个一级或二级标题）。"""
    for j in range(k + 1, len(L)):
        if re.match(r"#{1,2} ", L[j]):
            return j
    return len(L)


def _insert(L, at, block):
    if at > 0 and L[at - 1].strip():
        block = [""] + block
    L[at:at] = block


def _card_cells(L, card):
    """Corvio 读回来的表格是 corvio-table（YAML）：找到内容是 key 的单元格，改它右边那格。"""
    for key, row in card.items():
        val = row.strip("| ").split(" | ", 1)[1]
        for k in range(1, len(L) - 1):
            if L[k].strip() == key and L[k - 1].strip() == "content: |":
                j = next((j for j in range(k + 1, len(L)) if L[j].strip() == "content: |"), None)
                if j is not None and j + 1 < len(L):
                    ind = L[j + 1][:len(L[j + 1]) - len(L[j + 1].lstrip())]
                    L[j + 1] = ind + val
                break


def splice(md, b, new, card, swap):
    """md 是页面当前内容；new 是要插入的新划线和想法；card 是要刷新的行；swap 是 {旧图片 id: 新图片 id}。"""
    for old, nw in swap.items():
        md = md.replace(old, nw)
    L = md.split("\n")
    for k, l in enumerate(L):
        for key, row in card.items():
            if l.startswith(f"| {key} |"):
                L[k] = row
    _card_cells(L, card)
    if _sec(L, "我的笔记") is None:
        L += ["## 我的笔记", ""]
    chidx = {i["chapter"]: i["chapterIdx"] for i in b["items"]}
    for ch, items in by_chapter(new).items():
        quoted = {one(i["abstract"]) for i in b["items"] if i["chapter"] == ch and i["kind"] == "想法" and i.get("abstract")}
        block = note_md(items, quoted=quoted)
        k = _sec(L, "我的笔记"); e = _end(L, k)
        h = next((j for j in range(k + 1, e) if L[j].strip() == f"### {ch}"), None)
        if h is not None:
            _insert(L, next((j for j in range(h + 1, e) if L[j].startswith("#")), e), block)
        elif block:
            at = next((j for j in range(k + 1, e) if L[j].startswith("### ") and chidx.get(L[j][4:].strip(), 9999) > chidx.get(ch, 9999)), e)
            _insert(L, at, [f"### {ch}", ""] + block)
    return "\n".join(L)


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--workspace", help="Corvio 工作区 id，第一次必须给，之后记在 state.json 里")
    ap.add_argument("--name", help="图书馆名，默认「我的图书馆」")
    ap.add_argument("--include-private", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-overview", action="store_true", help="总览页被人改过也照样覆盖")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    books, by, sel, cur, st = context(a.data, a.work, a.include_private)
    st.setdefault("books", {})
    ws = a.workspace or st.get("workspace")
    if not a.dry_run:  # 预览不需要 CLI 和工作区
        if not shutil.which("corvio"):
            sys.exit("没有找到 corvio CLI（需要 Node.js）。安装：npm i -g @corvio/cli，然后 corvio auth login。只接了 Corvio MCP 的话，按 SKILL.md 的 MCP 方式写入。")
        if not ws:
            sys.exit("第一次写入请用 --workspace 指定工作区 id（可以用 `corvio workspaces list` 查看）。")
    if st.get("workspace") and ws and st["workspace"] != ws:
        sys.exit(f"这个 work 目录已经写进了工作区 {st['workspace']}，不能换成 {ws}。要写进新工作区，请换一个新的 --work 目录。")
    st["workspace"] = ws
    lib = st.setdefault("lib", uuid.uuid4().hex[:12])  # 幂等 key 用它，同一工作区里重建图书馆也不会撞上旧页面
    name = st["name"] = a.name or st.get("name") or "我的图书馆"
    cv = Corvio(ws)
    img = lambda f: os.path.join(a.work, "images", f)

    def save():
        with LOCK:
            save_state(a.work, st)

    # 1. 要上传的图片：新的，或者内容变了的
    ups = []
    if os.path.exists(img("library-cover.jpg")) and (st.get("cover") or {}).get("sha") != sha(img("library-cover.jpg")):
        ups.append(("cover", img("library-cover.jpg")))
    for s in sel:
        p = img(f'exlibris-{s["no"]:03d}.jpg')
        if os.path.exists(p) and (st["books"].get(s["id"]) or {}).get("plate_sha") != sha(p):
            ups.append((s["id"], p))
    todo = [s for s in sel if not (st["books"].get(s["id"]) or {}).get("id")]
    old = [s for s in sel if s not in todo]
    print(f"计划：上传 {len(ups)} 张图片，新建 {len(todo)} 个书页{'和总览页' if not st.get('home') else ''}，检查 {len(old)} 个已有书页的更新。"
          f"预计 {10 + len(ups) * 8 // a.workers + len(todo) * 10 + len(old) * 2} 秒左右。")  # 按 2026-10 实测：6 张图 + 5 个书页约 90 秒
    if a.dry_run:
        return
    swaps = {}

    def do_upload(job):
        key, p = job
        aid = cv.upload(p)
        with LOCK:
            if key == "cover":
                st["cover"] = {"asset_id": aid, "sha": sha(p)}
            else:
                bs = st["books"].setdefault(key, {})
                if bs.get("asset_id") and bs.get("id"):
                    swaps[key] = (bs["asset_id"], aid)
                bs.update(asset_id=aid, plate_sha=sha(p))
        save()
        print(f"  上传 {os.path.basename(p)}")
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(do_upload, ups))

    # 2. 总览页占位
    if not (st.get("home") or {}).get("id"):
        st["home"] = cv.create(name, "图书馆正在布置中……\n", key=f"weread-library:{lib}:home")
        save()
        print(f"  新建总览页 {st['home']['url']}")

    # 3a. 新书页
    def do_create(s):
        b = by[s["id"]]
        r = cv.create(cur[s["id"]].get("short") or short_title(b["title"]), render_book(s, by, cur, st),
                      st["home"]["node_id"], key=f"weread-library:{lib}:{s['id']}")
        with LOCK:
            st["books"].setdefault(s["id"], {}).update(r, no=s["no"], written=[i["id"] for i in b["items"]], card=card_rows(b))
        save()
        print(f"  新建书页 No.{s['no']:03d}《{b['title']}》")
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(do_create, todo))
    # 新书页里「相关馆藏」要链到别的书，那些书的地址要等全部建完才知道，所以补一次
    for s in todo:
        if cur[s["id"]].get("related"):
            bs = st["books"][s["id"]]
            bs["revision"] = cv.update(bs["id"], render_book(s, by, cur, st), bs["revision"], "补上相关馆藏的链接")
            save()

    # 3b. 已有书页：插入新划线和想法，刷新卡片和藏书票
    added = {}
    for s in old:
        b, bs = by[s["id"]], st["books"][s["id"]]
        written = set(bs.get("written") or [])
        new = [i for i in b["items"] if i["id"] not in written]
        card = card_rows(b)
        swap = dict([swaps[s["id"]]]) if s["id"] in swaps else {}
        if not new and not swap and card == bs.get("card"):
            continue
        md, rev = cv.get(bs["id"])
        if not bs.get("written"):  # state 是旧的或丢了：按文本判断哪些已经在页面上
            new = [i for i in new if one(i["text"]) not in md]
        bs["revision"] = cv.update(bs["id"], splice(md, b, new, card, swap), rev,
                                   f"weread-library 同步：新增 {len(new)} 条划线和想法" if new else "weread-library 同步：刷新阅读数据")
        bs.update(written=[i["id"] for i in b["items"]], card=card)
        save()
        if new:
            added[b["title"]] = len(new)
            print(f"  更新书页《{b['title']}》：新增 {len(new)} 条")

    # 4. 总览页
    md = overview(a.data, a.include_private, sel, by, books, cur, st, name)
    h = st["home"]
    cur_md, rev = cv.get(h["id"])
    if h.get("sha") and rev != h["revision"] and not a.force_overview:
        print(f"⚠️ 总览页在上次写入之后被改过（revision {h['revision']} → {rev}），这次没有覆盖。确认可以覆盖后加 --force-overview 重跑。", file=sys.stderr)
    elif hashlib.sha256(md.encode()).hexdigest()[:16] != h.get("sha") or rev != h["revision"]:
        h["revision"] = cv.update(h["id"], md, rev, "weread-library：刷新总览页")
        h["sha"] = hashlib.sha256(md.encode()).hexdigest()[:16]
        save()
        print("  更新总览页")
    save()
    print(json.dumps({"home": h["url"], "books_on_shelf": len(sel), "created": len(todo), "images_uploaded": len(ups),
                      "updated_books": added, "not_on_shelf": facts(a.data, a.include_private)["noted"] - len(sel)}, ensure_ascii=False))


def record(argv):
    """MCP 方式写入时，把返回结果记进 state.json：publish.py record --work W key=value ...
    key 用点号表示层级，例如 home.url=... books.<bookId>.asset_id=... cover.asset_id=..."""
    ap = argparse.ArgumentParser(prog="publish.py record")
    ap.add_argument("--work", required=True)
    ap.add_argument("pairs", nargs="+")
    a = ap.parse_args(argv)
    from pages import load_state
    st = load_state(a.work)
    for p in a.pairs:
        k, _, v = p.partition("=")
        parts = k.split(".")
        node = st
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = int(v) if v.isdigit() and parts[-1] in ("revision", "no") else v
    save_state(a.work, st)
    print("已记录")


def mcp_next(argv):
    """MCP 方式：根据 state.json 算出下一步要调用的 MCP 工具和参数。agent 循环「next → 调用 → 运行 then 里的命令」，直到 done。"""
    ap = argparse.ArgumentParser(prog="publish.py next")
    ap.add_argument("--data", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--include-private", action="store_true")
    a = ap.parse_args(argv)
    books, by, sel, cur, st = context(a.data, a.work, a.include_private)
    st.setdefault("books", {})
    S = os.path.dirname(os.path.abspath(__file__))
    W, D = a.work, a.data
    rec = f"python3 {S}/publish.py record --work {W}"
    ws, name = st.get("workspace"), st.get("name") or "我的图书馆"
    if not st.get("lib"):
        st["lib"] = uuid.uuid4().hex[:12]
        save_state(W, st)
    lib = st["lib"]
    out = lambda **k: print(json.dumps(k, ensure_ascii=False, indent=1)) or sys.exit(0)
    if not ws:
        out(step="选工作区", say="调用 list_workspaces，用它推荐的默认可写工作区（不用问用户；没有可写工作区时才问），然后运行 then。",
            then=[f'{rec} workspace=<工作区 id> name="{name}"'])
    img = lambda f: os.path.join(W, "images", f)
    jobs = [("cover", img("library-cover.jpg"))] + [(s["id"], img(f'exlibris-{s["no"]:03d}.jpg')) for s in sel]
    for key, p in jobs:
        have = st.get("cover") if key == "cover" else st["books"].get(key)
        if os.path.exists(p) and not (have or {}).get("asset_id"):
            k = "cover" if key == "cover" else f"books.{key}"
            out(step=f"上传图片 {os.path.basename(p)}",
                tool="prepare_file_upload", args={"file_name": os.path.basename(p), "mime_type": "image/jpeg",
                                                  "size_bytes": os.path.getsize(p), "workspace_id": ws},
                then=[f"python3 {S}/put.py {p} '<prepare_file_upload 返回的 JSON>'",
                      "用 put.py 打印的参数调用 finalize_file_upload",
                      f"{rec} {k}.asset_id=<finalize 返回的 asset_id> {k}.plate_sha={sha(p)}" if key != "cover" else
                      f"{rec} cover.asset_id=<finalize 返回的 asset_id> cover.sha={sha(p)}"])
    if not (st.get("home") or {}).get("id"):
        out(step="建总览页占位", tool="create_document",
            args={"title": name, "markdown": "图书馆正在布置中……", "workspace_id": ws, "idempotency_key": f"weread-library:{lib}:home"},
            then=[f"{rec} home.id=<返回的 id> home.node_id=<node_id> home.url=<url> home.revision=<revision>"])
    md_dir = os.path.join(W, "md"); os.makedirs(md_dir, exist_ok=True)
    for s in sel:
        bs = st["books"].get(s["id"]) or {}
        if bs.get("id"):
            continue
        p = os.path.join(md_dir, f'{s["no"]:03d}.md')
        open(p, "w", encoding="utf-8").write(render_book(s, by, cur, st))
        k = f"books.{s['id']}"
        out(step=f"建书页 No.{s['no']:03d}（还剩 {sum(1 for x in sel if not (st['books'].get(x['id']) or {}).get('id'))} 本）",
            tool="create_document", args={"title": cur[s["id"]].get("short") or short_title(by[s["id"]]["title"]),
                                          "parent_node_id": st["home"]["node_id"], "workspace_id": ws,
                                          "idempotency_key": f"weread-library:{lib}:{s['id']}", "markdown": f"<{p} 的完整内容>"},
            then=[f"{rec} {k}.id=<返回的 id> {k}.node_id=<node_id> {k}.url=<url> {k}.revision=<revision> {k}.no={s['no']}",
                  f"python3 {S}/publish.py synced --data {D} --work {W} --book {s['id']}"])
    for s in sel:
        b, bs = by[s["id"]], st["books"][s["id"]]
        new = [i for i in b["items"] if i["id"] not in set(bs.get("written") or [])]
        if new or card_rows(b) != bs.get("card"):
            page, newp = os.path.join(md_dir, f'{s["no"]:03d}.current.md'), os.path.join(md_dir, f'{s["no"]:03d}.synced.md')
            out(step=f"同步《{b['title']}》：{len(new)} 条新内容", tool="read_document", args={"id": bs["id"], "mode": "full"},
                then=[f"把读到的页面正文原样存成 {page}，记下 revision",
                      f"python3 {S}/publish.py splice --data {D} --work {W} --book {s['id']} --page {page} --out {newp}",
                      f"update_document：id={bs['id']}，expected_content_revision=<刚才的 revision>，markdown=<{newp} 的完整内容>，change_summary=同步新划线",
                      f"python3 {S}/publish.py synced --data {D} --work {W} --book {s['id']} revision=<update 返回的 revision>"])
    md = overview(D, a.include_private, sel, by, books, cur, st, name)
    h = hashlib.sha256(md.encode()).hexdigest()[:16]
    if h != st["home"].get("sha"):
        p = os.path.join(md_dir, "overview.md")
        open(p, "w", encoding="utf-8").write(md)
        out(step="写总览页", tool="update_document",
            args={"id": st["home"]["id"], "expected_content_revision": st["home"]["revision"], "markdown": f"<{p} 的完整内容>",
                  "change_summary": "weread-library：刷新总览页"},
            then=[f"{rec} home.revision=<返回的 revision> home.sha={h}"],
            note="如果报 revision 不一致，说明总览页被人改过：先问用户是否覆盖，同意后用 read_document 取最新 revision 再写。")
    out(step="完成", done=True, home=st["home"].get("url"), books=len(sel))


def mcp_sync(argv):
    """MCP 方式的同步：splice 生成插好新内容的页面，写回成功后用 synced 记下来。"""
    ap = argparse.ArgumentParser(prog="publish.py splice|synced")
    ap.add_argument("cmd", choices=["splice", "synced"])
    ap.add_argument("--data", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--book", required=True)
    ap.add_argument("--page", help="splice：页面当前内容")
    ap.add_argument("--out", help="splice：输出文件")
    ap.add_argument("--include-private", action="store_true")
    ap.add_argument("pairs", nargs="*", help="synced：例如 revision=12")
    a = ap.parse_args(argv)
    books, by, sel, cur, st = context(a.data, a.work, a.include_private)
    b, bs = by[a.book], st.setdefault("books", {}).setdefault(a.book, {})
    if a.cmd == "splice":
        md = open(a.page, encoding="utf-8").read()
        new = [i for i in b["items"] if i["id"] not in set(bs.get("written") or []) and one(i["text"]) not in md]
        open(a.out, "w", encoding="utf-8").write(splice(md, b, new, card_rows(b), {}))
        print(f"新增 {len(new)} 条，已写到 {a.out}")
    else:
        bs.update(written=[i["id"] for i in b["items"]], card=card_rows(b))
        for p in a.pairs:
            k, _, v = p.partition("=")
            bs[k] = int(v) if v.isdigit() else v
        save_state(a.work, st)
        print("已记录")


if __name__ == "__main__":
    if sys.argv[1:2] == ["record"]:
        record(sys.argv[2:])
    elif sys.argv[1:2] == ["next"]:
        mcp_next(sys.argv[2:])
    elif sys.argv[1:2] in (["splice"], ["synced"]):
        mcp_sync(sys.argv[1:])
    else:
        main()
