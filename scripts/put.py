#!/usr/bin/env python3
"""把本地文件 PUT 到 Corvio prepare_file_upload 返回的签名地址。文件内容不经过模型上下文。

用法：python3 put.py <本地文件> '<prepare_file_upload 返回的 JSON>'
  也可以把 JSON 从标准输入传进来：python3 put.py <本地文件> - < receipt.json
成功时打印 finalize_file_upload 需要的参数（upload_id、blob_name、file_name、size_bytes、mime_type）。
不会打印上传凭证。
"""
import json, os, sys, time, urllib.error, urllib.request

f, raw = sys.argv[1], sys.argv[2]
r = json.loads(sys.stdin.read() if raw == "-" else raw)
data = open(f, "rb").read()
for k in range(4):
    req = urllib.request.Request(r["upload_url"], data=data, method=r.get("upload_method", "PUT"), headers=r["upload_headers"])
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status < 300:
                break
            err = f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403, 404, 410):
            sys.exit(f"上传失败：HTTP {e.code}。上传地址可能已过期，请重新调用 prepare_file_upload。")
        err = f"HTTP {e.code}"
    except Exception as e:
        err = type(e).__name__
    time.sleep(2 * (k + 1))
else:
    sys.exit(f"上传失败（{err}），重试了 4 次。网络可能不稳定，请稍后重新运行同一条命令。")
blob = r["blob_name"]
print(json.dumps({"upload_id": r["upload_id"], "blob_name": blob, "file_name": os.path.basename(f),
                  "size_bytes": len(data), "mime_type": r["upload_headers"].get("Content-Type", "application/octet-stream"),
                  "workspace_id": blob.split("/")[1] if blob.startswith("workspace-assets/") else None}, ensure_ascii=False))
