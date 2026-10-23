#!/usr/bin/env python3
"""从剪贴板读取微信读书 Key，写到 ~/.config/weread-library/key。不会打印 Key。"""
import argparse, os, re, subprocess, sys

KEY_RE = re.compile(r"wrk-[A-Za-z0-9_.-]+")
KEY_PATH = os.path.expanduser("~/.config/weread-library/key")
MANUAL = ("mkdir -p ~/.config/weread-library && printf '%s' 'wrk-你的Key' "
          "> ~/.config/weread-library/key && chmod 600 ~/.config/weread-library/key")


def clipboard_cmds():
    if sys.platform == "darwin":
        return [["pbpaste"]]
    if sys.platform == "win32":
        return [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]
    return [["wl-paste"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b"]]


def read_clipboard():
    """读到内容返回字符串；工具都不可用返回 None。空剪贴板返回空字符串。"""
    saw_tool = False
    for cmd in clipboard_cmds():
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=10)
        except (FileNotFoundError, OSError):
            continue
        saw_tool = True
        if r.returncode == 0:
            return r.stdout.decode("utf-8", errors="ignore")
    return "" if saw_tool else None


def has_key():
    if KEY_RE.search(os.environ.get("WEREAD_API_KEY") or ""):
        return True
    if not os.path.exists(KEY_PATH):
        return False
    try:
        text = open(KEY_PATH, encoding="utf-8", errors="ignore").read()
    except OSError:
        return False
    return bool(KEY_RE.search(text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查本机是否已有 Key")
    a = ap.parse_args()
    if a.check:
        if has_key():
            print("已有 Key")
            return
        sys.exit("还没有 Key")
    text = read_clipboard()
    if text is None:
        print("读不到剪贴板。请手动执行：")
        print(MANUAL)
        sys.exit(1)
    m = KEY_RE.search(text)
    if not m:
        print("剪贴板里没有找到以 wrk- 开头的 Key。请在 https://weread.qq.com/r/weread-skills 扫码后点复制，再运行一次。")
        sys.exit(1)
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, m.group(0).encode())
    finally:
        os.close(fd)
    os.chmod(KEY_PATH, 0o600)
    print("已保存微信读书 Key（只保存在你的电脑上）")


if __name__ == "__main__":
    main()
