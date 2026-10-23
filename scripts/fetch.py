#!/usr/bin/env python3
"""从微信读书官方 Agent Gateway 拉取书架、笔记和阅读统计，存成本地 JSON。

用法：
  python3 fetch.py --out ./weread-data              # 首次全量
  python3 fetch.py --out ./weread-data --top 5      # 快速：只拉笔记最多的 5 本
  python3 fetch.py --out ./weread-data --refresh    # 增量：只重拉笔记有变化的书

Key 的读取顺序（不会打印 Key）：
  1. 环境变量 WEREAD_API_KEY
  2. --key-file 指定的文件
  3. ~/.config/weread-library/key
文件里只要含有一段 wrk-... 即可。
"""
import argparse, json, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
DEFAULT_KEY_FILE = os.path.expanduser("~/.config/weread-library/key")

NET_ERR = "网络或接口出错，请稍后重跑"


def is_error(obj):
    return isinstance(obj, dict) and "_error" in obj


def book_incomplete(rec):
    if not isinstance(rec, dict):
        return True
    for k in ("info", "progress", "bookmarks", "chapters"):
        if is_error(rec.get(k)):
            return True
    return bool(rec.get("_reviews_error"))


def file_incomplete(fp):
    try:
        rec = json.load(open(fp, encoding="utf-8"))
    except Exception:
        return True
    return book_incomplete(rec)


def should_skip_book(fp, bid, changed):
    return os.path.exists(fp) and str(bid) not in changed and not file_incomplete(fp)


def pull_reviews(cli, bid):
    revs, sk, err = [], 0, None
    for _ in range(50):
        r = cli.call("/review/list/mine", bookid=bid, count=20, synckey=sk)
        if is_error(r):
            err = r
            break
        revs += r.get("reviews") or []
        if not r.get("hasMore"):
            break
        sk = r.get("synckey") or 0
    return revs, err


def fetch_shelf(cli):
    r = cli.call("/shelf/sync")
    if is_error(r):
        sys.exit(NET_ERR)
    return r


def fetch_notebooks(cli):
    books, last, meta = [], None, {}
    for _ in range(100):
        r = cli.call("/user/notebooks", count=20, **({"lastSort": last} if last else {}))
        if is_error(r):
            sys.exit(NET_ERR)
        bs = r.get("books") or []
        books += bs
        meta = {k: r.get(k) for k in ("totalBookCount", "totalNoteCount")}
        if not r.get("hasMore") or not bs:
            break
        last = bs[-1].get("sort")
        if last is None:
            break
    return {"meta": meta, "books": books}

def find_key(key_file):
    cands = [os.environ.get("WEREAD_API_KEY", "")]
    for f in [key_file, DEFAULT_KEY_FILE]:
        if f and os.path.exists(f):
            cands.append(open(f, encoding="utf-8", errors="ignore").read())
    for c in cands:
        m = re.search(r"wrk-[A-Za-z0-9_.-]+", c or "")
        if m:
            return m.group(0)
    sys.exit("没有找到微信读书 API Key。请设置环境变量 WEREAD_API_KEY，"
             f"或把 Key 写进 {DEFAULT_KEY_FILE}（chmod 600）。获取方式：https://weread.qq.com/r/weread-skills")


class Client:
    def __init__(self, key):
        self.key = key

    def call(self, api, **params):
        body = json.dumps(dict(api_name=api, **params)).encode()
        req = urllib.request.Request(GATEWAY, data=body, headers={
            "Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        err = None
        for i in range(4):
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    return json.loads(r.read().decode("utf-8"), strict=False)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    sys.exit("微信读书 Key 无效或已过期（HTTP %d），请重新获取。" % e.code)
                err = e
            except Exception as e:
                err = e
            time.sleep(2 * (i + 1))
        return {"_error": str(err)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--key-file")
    ap.add_argument("--refresh", action="store_true", help="重新拉书架和笔记本，并重拉笔记数有变化的书")
    ap.add_argument("--top", type=int, help="只拉笔记最多的前 N 本书的详细内容")
    ap.add_argument("--include-private", action="store_true", help="包含私密书架上的书（默认排除，只在 --top 时影响选书）")
    ap.add_argument("--no-shelf-info", action="store_true", help="不拉没有笔记的书的基本信息（更快）")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    out = a.out
    os.makedirs(os.path.join(out, "books"), exist_ok=True)
    os.makedirs(os.path.join(out, "info"), exist_ok=True)
    cli = Client(find_key(a.key_file))

    def path(n): return os.path.join(out, n)
    def have(n): return os.path.exists(path(n))
    def save(n, obj): json.dump(obj, open(path(n), "w", encoding="utf-8"), ensure_ascii=False)

    old_nb = {}
    if a.refresh and have("notebooks.json"):
        old_nb = {str(b["bookId"]): b for b in json.load(open(path("notebooks.json")))["books"]}

    if a.refresh or not have("shelf.json"):
        save("shelf.json", fetch_shelf(cli))
    if a.refresh or not have("notebooks.json"):
        save("notebooks.json", fetch_notebooks(cli))
    for mode in ("overall", "annually"):
        if a.refresh or not have(f"readdata_{mode}.json"):
            save(f"readdata_{mode}.json", cli.call("/readdata/detail", mode=mode))

    nb = json.load(open(path("notebooks.json")))["books"]
    changed = []
    for b in nb:
        bid = str(b.get("bookId"))
        o = old_nb.get(bid)
        if a.refresh and (o is None or (o.get("noteCount"), o.get("bookmarkCount"), o.get("reviewCount"))
                          != (b.get("noteCount"), b.get("bookmarkCount"), b.get("reviewCount"))):
            changed.append(bid)
    changed_set = set(changed)

    shelf_books = json.load(open(path("shelf.json"), encoding="utf-8")).get("books") or []
    private = {str(b.get("bookId")) for b in shelf_books if b.get("secret")}
    all_ids = [str(b["bookId"]) for b in nb if b.get("bookId")]
    if a.top is not None:
        cand = [b for b in nb if b.get("bookId") and (a.include_private or str(b["bookId"]) not in private)]
        cand.sort(key=lambda b: -(b.get("noteCount") or 0))
        ids = [str(b["bookId"]) for b in cand[:a.top if a.top > 0 else 0]]
    else:
        ids = all_ids

    def fetch_book(bid):
        f = f"books/{bid}.json"
        if should_skip_book(path(f), bid, changed):
            return
        rec = {"bookId": bid, "fetchedAt": int(time.time()),
               "info": cli.call("/book/info", bookId=bid),
               "progress": cli.call("/book/getprogress", bookId=bid),
               "bookmarks": cli.call("/book/bookmarklist", bookId=bid),
               "chapters": cli.call("/book/chapterinfo", bookId=bid)}
        revs, err = pull_reviews(cli, bid)
        rec["reviews"] = revs
        if err:
            rec["_reviews_error"] = err
        save(f, rec)

    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(fetch_book, ids))

    if a.top is None and not a.no_shelf_info:
        shelf = json.load(open(path("shelf.json"))).get("books") or []
        def fetch_info(b):
            bid = str(b.get("bookId"))
            if have(f"info/{bid}.json") or have(f"books/{bid}.json"):
                return
            save(f"info/{bid}.json", cli.call("/book/info", bookId=bid))
        with ThreadPoolExecutor(a.workers) as ex:
            list(ex.map(fetch_info, shelf))

    errs = sum(1 for f in os.listdir(path("books")) if file_incomplete(path("books/" + f)))
    if a.top is not None:
        partial = True
        save("fetch_state.json", {"partial": True, "top": a.top})
    elif errs == 0:
        partial = False
        save("fetch_state.json", {"partial": False})
    elif have("fetch_state.json"):
        st = json.load(open(path("fetch_state.json"), encoding="utf-8"))
        partial = bool(st.get("partial")) if isinstance(st, dict) else False
    else:
        partial = False
    if errs > 0:
        print("有 %d 本书没拉完整，再运行一次同样的命令会自动补拉。" % errs, file=sys.stderr)
    print(json.dumps({"shelf": len(json.load(open(path("shelf.json"))).get("books") or []),
                      "notebook_books": len(all_ids),
                      "refetched": len([i for i in ids if i in changed_set]) if a.refresh else len(ids),
                      "books_with_errors": errs, "partial": partial}, ensure_ascii=False))


if __name__ == "__main__":
    main()
