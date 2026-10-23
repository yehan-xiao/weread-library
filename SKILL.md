---
name: weread-library
description: 把微信读书的划线和想法整理成一座私人图书馆（书墙封面 + 每本书一页藏书票和按章节整理的笔记），建在 Corvio 上，之后能增量同步，AI 也能读取。先上架 5 本让用户看到效果，再按需整理全部。用户说"把我的微信读书整理进 Corvio""把微信读书笔记做成图书馆""整理/导出我的微信读书划线""把剩下的书也整理进来""同步一下微信读书"时使用。
---

# 微信读书私人图书馆

把用户在微信读书里的划线和想法，整理成 Corvio 里一座有个人感、值得截图分享的图书馆：

- **总览页**（默认叫「我的图书馆」）：书脊封面图、一句开馆语、我的阅读、书架、读得最久的书、怎么用这座图书馆。
- **每本书一页**：藏书票（真实书封）、这本书、这本书和你、我的笔记。

**用户很可能是第一次用 skill，也是第一次用 Corvio。** 所以：
- 能自动做的都由你来做，不要让用户敲命令、选技术方案；
- 跟用户说话用大白话，不提 CLI、MCP、JSON、工作区 id 这类词；
- 整个过程只需要用户做三件事：登录 Corvio、扫码拿 Key 并复制、（可选）给图书馆起个名字；
- 先上架 5 本书，让用户一两分钟内看到效果，满意了再整理全部。

脚本都在 `scripts/` 下，用 `python3` 运行。下文 `$S` 指本 skill 的 `scripts/` 目录，`$D` 是 `~/weread-library/data`，`$W` 是 `~/weread-library/work`。**同一座图书馆始终用同一个 `$W`**：里面的 `state.json` 记着它写进了 Corvio 的哪些页面，之后追加和同步都靠它。

先判断用户要做什么：
- 第一次建图书馆 → 第 0–5 步；
- 「把剩下的书也整理进来」「再上架几本」 → 第 6 步；
- 「同步一下」 → 第 7 步。

## 0. 准备（你来检查，用户只在需要时动手）

开始前用一句话告诉用户接下来会发生什么，例如：「我来帮你把微信读书做成一座图书馆。中间需要你做两件小事：登录 Corvio、复制一个微信读书的 Key。先做 5 本书，一两分钟就好。」

按顺序检查，已经满足的就跳过，不用告诉用户：

1. **Python**：`python3 --version` 能运行就行。不能运行时，告诉用户需要先装 Python 3（macOS 可以用 `xcode-select --install`），然后停下。
2. **图片依赖**：`python3 -c "import PIL"` 不报错就用 `python3` 出图。报错就建一个专用环境（macOS 自带的 Homebrew Python 不允许直接 pip 安装）：`python3 -m venv ~/weread-library/.venv && ~/weread-library/.venv/bin/pip install -q pillow`，之后第 4 步的 `render.py` 改用 `~/weread-library/.venv/bin/python` 运行，其他脚本照旧用 `python3`。不需要装 playwright。实在装不上也没关系，跳过第 4 步，图书馆照样能建，只是没有封面和藏书票。
3. **连上 Corvio**，二选一，优先第一种：
   - **CLI 方式（快）**：`corvio auth status --json` 显示 `authenticated: true` 就好了。没有 corvio 命令但有 npm（Node.js）时，由你运行 `npm i -g @corvio/cli`；然后运行 `corvio auth login`，它会打开浏览器，告诉用户：「浏览器里打开了 Corvio，登录或注册后点一下授权就行，没有账号的话用邮箱注册一个，很快。」等命令结束后再检查一次。
   - **MCP 方式（慢一些）**：没有 npm，或者用户的环境装不了命令行工具，但你已经连着 Corvio MCP（有 `create_document` 等工具）时用这种。都没有时，告诉用户在自己的 AI 工具里添加 Corvio 连接器，地址是 `https://api.corvio.ai/mcp`，然后重新开始。
4. **微信读书 Key**：运行 `python3 $S/save_key.py --check`，显示「已有 Key」就跳过；显示「还没有 Key」时：
   - 由你打开 https://weread.qq.com/r/weread-skills （macOS 用 `open <链接>`），告诉用户：「请用微信扫码，拿到以 wrk- 开头的 Key 后点复制，复制好了跟我说一声。不用发给我。」
   - 用户说复制好了，运行 `python3 $S/save_key.py`。它直接从剪贴板读取并存到本地，Key 不会出现在对话里。失败时按它的提示再试一次。
   - **不要让用户把 Key 发在聊天里**；如果用户已经发了，帮用户存好，并提醒之后去重新生成一个。不要读取、显示或复述 Key。
5. **图书馆的名字**：在等用户扫码的时候顺便问一句：「你的图书馆想叫什么名字？比如『小林的图书馆』，不想起名的话就叫『我的图书馆』。」名字会印在封面上。**不要自己猜用户的名字。**

写进 Corvio 的哪个工作区不用问用户：CLI 方式下，运行 `corvio workspaces list --json`，用 `is_default` 为 true 且 `can_write` 为 true 的那个；没有的话用第一个 `can_write` 为 true 的个人工作区。MCP 方式下不传 `workspace_id`，Corvio 会用用户的默认工作区。

## 1. 拉数据（先拉 5 本）

```bash
python3 $S/fetch.py --out $D --top 5
```

只拉笔记最多的 5 本书，加上书架和阅读统计，通常不到一分钟。中途出错就再运行一次同样的命令。

**私密书架上的书默认不进图书馆。** 用户明确要求时，后续所有命令都加 `--include-private`。

## 2. 选书上架

```bash
python3 $S/library.py select --data $D --work $W --top 5
```

已经有图书馆时这条命令会拒绝执行，改用第 6 步。

## 3. 策展（你来写，需要你的判断）

```bash
python3 $S/library.py digest --data $D --work $W
```

读输出，按 `prompts/curation.md` 的规则，为每本书写「最舍不得的一句」和「这本书和你」，存成 `$W/curation.json`。然后**必须**核对：

```bash
python3 $S/library.py check --data $D --work $W
```

直到显示「全部通过」。报错的地方逐个改：引文必须逐字来自划线或想法，数字必须能在数据里找到，找不到就删掉那句话或换一种不带数字的说法。

这一步是用户第一眼看到的内容。一个写错的日期或数字，就会让用户不再相信整座图书馆。

## 4. 生成图片

```bash
python3 $S/render.py --data $D --work $W --name "<图书馆名>"
```

输出 `$W/images/library-cover.jpg`（书脊封面）和 `$W/images/exlibris-NNN.jpg`（藏书票）。生成后用你的看图能力打开封面和一张藏书票看一眼，确认没有方框乱码、文字没有溢出。

## 5. 写进 Corvio

**CLI 方式**：

```bash
python3 $S/publish.py --data $D --work $W --workspace <工作区 id> --name "<图书馆名>"
```

它会上传图片、建总览页和书页、补上书与书之间的链接、写好总览页，5 本书一般 1–2 分钟（开头会打印预计时间）。进度记在 `$W/state.json`，失败了直接重跑同一条命令，不会重复建页。

**MCP 方式**：见文末附录。

**交付**，这是用户最兴奋的时刻，说得具体一点：
- 给出总览页链接（输出里的 `home`），说一句「你的图书馆开馆了，先上架了你笔记最多的 5 本书」；
- 告诉用户可以截图分享：封面和每本书的藏书票最好看；
- 问一句：「你还有 N 本书留有笔记，要把它们也整理进来吗？大约需要 X 分钟。」（N 是 `publish.py` 最后输出里的 `not_on_shelf`；X 按补全数据 10 分钟、之后每 20 本约 5 分钟估算）
- 再提一句以后怎么用：在任何连了 Corvio 的 AI 里问「我在哪本书里读到过关于『选择』的话？」就能用上这些笔记。

## 6. 整理更多的书（「把剩下的也整理进来」「再上架 10 本」）

先补全数据（快速开馆时只拉了 5 本）。书多时要几分钟，告诉用户大概要等多久：

```bash
python3 $S/fetch.py --out $D
```

然后**每批 20 本**上架（用户只要 10 本就用 10），每批走一遍：

```bash
python3 $S/library.py select --data $D --work $W --add 20
python3 $S/library.py digest --data $D --work $W --only-new
```

只为这批新书写策展，**合并**进 `$W/curation.json`（旧书的条目不要动），然后 `check`、`render.py`、`publish.py`（不用再给 `--workspace` 和 `--name`）。已上架的书编号不变。

每批写完告诉用户一声「又上架了 20 本，还剩 N 本」，用户刷新页面就能看到。数据补全后，总览页会补齐开馆时间和年份分布，封面上的书架也会被填满。

## 7. 增量同步（用户说「同步一下」）

```bash
python3 $S/fetch.py --out $D --refresh
python3 $S/render.py --data $D --work $W
python3 $S/publish.py --data $D --work $W
```

`publish.py` 会把每本已上架的书里**新增的**划线和想法插到「我的笔记」对应章节末尾，并刷新「这本书」里的阅读和笔记两行。用户在页面上自己写的内容原样保留。总览页会整页刷新；如果它提示总览页被人改过，问用户是否覆盖，同意后加 `--force-overview` 重跑。

最后告诉用户这次哪几本书新增了多少条（看输出里的 `updated_books`）。如果有新读的书笔记很多，可以问用户要不要按第 6 步上架。

在微信读书里删掉的划线，不会从 Corvio 页面里删掉。

## 边界

- Key 只存在用户本地，不要写进 Corvio，也不要出现在对话里。
- 图书馆里的事实只能来自微信读书数据。AI 写的部分（「这本书和你」）在页面上已经标注了来源。
- 不要上传书的正文内容。划线和想法本身属于用户的笔记，书封只在用户自己的工作区里展示。
- 用户没有明确同意时，不要把私密书架上的书放进图书馆。

## 附录：MCP 方式（没有 corvio CLI 时）

第 5 步和第 7 步改成下面这个循环，其余步骤不变。每次 MCP 调用要二三十秒，10 本书大约要 10–15 分钟，开始前告诉用户。

```bash
python3 $S/publish.py next --data $D --work $W
```

它会打印一个 JSON，告诉你**下一步**做什么：
- `tool` / `args`：要调用的 Corvio MCP 工具和参数。`args` 里写着 `<某文件 的完整内容>` 的地方，读那个文件，把内容原样填进去。
- `then`：调用成功后要依次做的事，通常是运行 `publish.py record ...`，把返回的 id、url、revision 记进 `state.json`。命令里 `<...>` 的部分换成工具实际返回的值。
- `say`：需要先问用户的事。

做完 `then`，再运行一次 `next`，直到它返回 `"done": true`，然后按第 5 步的「交付」把 `home` 链接给用户。中途中断了也一样：重新运行 `next`，它会从没做完的地方继续。

几点注意：
- 上传图片时，用 `put.py` 把文件字节直接传到 `prepare_file_upload` 返回的地址，不要把图片读进上下文，也不要打印上传凭证。
- 第一次运行 `next` 时，它会让你先记录工作区：`python3 $S/publish.py record --work $W workspace=<id> name="我的图书馆"`。
- MCP 方式下，书页里的「相关馆藏」只显示书名、不带链接。
