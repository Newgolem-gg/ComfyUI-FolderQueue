# ComfyUI-FolderQueue

**ComfyUI 逐张队列 / One-prompt-per-image folder queue for ComfyUI.**

Turn 「one prompt that loops over a whole folder」 into 「one prompt per image」. After each image finishes, the node re-enqueues the *same workflow* with `start_index + 1`, so ComfyUI's normal inter-prompt cleanup runs between images: intermediate tensors are released, but the models stay loaded on the GPU.

结果：显存不再随张数线性上涨，也不会因为 for 循环把整批图的中间结果都攥在内存里而越跑越慢。

```
提交一次  →  第 1 张  →  自动排队第 2 张  →  …  →  第 N 张  →  结束
                ↑ 两次 prompt 之间，ComfyUI 会释放上一张图的中间张量
```

---

## Why

Running a folder through a `for` loop node keeps **every** intermediate alive for the whole run: VAE tiles, upscale-model output, cached latents. On a 24 GB card a 20-image batch of 4K upscales will simply run out of memory, or spend most of its time shuffling tensors.

Splitting into one prompt per image fixes that, but ComfyUI has no built-in way to queue "the next image". This node adds exactly that and nothing else.

---

## Node: `FolderQueueNext` — 逐张队列 / Folder Queue Next

| input | type | default | meaning |
|---|---|---|---|
| `images` | IMAGE | | **required** — wire from `SaveImage.images` so the node runs only after the current image is written |
| `start_index` | INT | 0 | index of the image currently being processed |
| `total` | INT | 0 | number of images. `0` = count the files in `directory` |
| `enable` | BOOLEAN | true | false = do not queue anything |
| `directory` (optional) | STRING | "" | folder path, used to count images when `total = 0` |
| `loader_node_id` (optional) | STRING | "" | explicit id of the loader node to bump. Empty = auto-detect |

Auto-detection looks for the **first node whose `class_type` starts with `LoadImageListFromDir`**.

Output: none (it is an `OUTPUT_NODE`, it only has side effects). A UI text readout is returned.

---

## How it works

1. ComfyUI hands the node the **API-format prompt** it is currently executing (`hidden` input `PROMPT`).
2. The prompt is deep-copied.
3. Every `LoadImageListFromDir*` node (or the one given by `loader_node_id`) gets `inputs.start_index = start_index + 1`.
4. The node sets its own `start_index` in the copy, so the next run continues the chain.
5. Output-node ids are recomputed, and the item is put into `PromptServer.instance.prompt_queue` through the server event loop (`loop.call_soon_threadsafe`).

Because it runs inside the executing graph, a crash in the current image simply means nothing further is queued — it can never make the current image fail (everything is wrapped in `try/except` and failures are only printed).

---

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/<your-name>/ComfyUI-FolderQueue.git
```

or drop the folder into `ComfyUI/custom_nodes/ComfyUI-FolderQueue/` and **restart ComfyUI**.

No third-party dependencies beyond ComfyUI itself.

### Companion node (recommended)

`LoadImageListFromDir` comes from [ComfyUI-Inspire-Pack](https://github.com/ltdrdata/ComfyUI-Inspire-Pack). If you use a different loader, set `loader_node_id` to its node id — the only requirement is that the node exposes an `start_index` integer input.

---

## Usage

1. Build your normal single-image workflow.
2. Add a `LoadImageListFromDir` node, point it at the folder, set `image_load_cap = 1`, `start_index = 0`.
3. Append `逐张队列 / Folder Queue Next`:
   ```
   Load Image List From Dir ──► (your workflow) ──► SaveImage
                                                        │
                                                        └─ images ──► FolderQueueNext
                                                                        directory ◄── folder path
   ```
4. Press **Queue once**. The console prints `[FolderQueue] 已排队 x/y` after every image and `[FolderQueue] 全部完成` at the end.

### Typical console output

```
[FolderQueue] 已排队下一张：2/24（start_index=1）
[FolderQueue] 已排队下一张：3/24（start_index=2）
...
[FolderQueue] 全部完成：24/24
```

---

## Caveats worth knowing

- **Seeds are frozen.** The re-queued prompt is a copy of the one that just ran, so any widget resolved to a concrete number (seed included) keeps that value for every image. If you want variation per image, derive the seed *inside* the graph from `start_index` (e.g. an expression node) instead of relying on a randomize button.
- **The node must run last.** Feed `images` from `SaveImage`; if you feed it something that evaluates early, the next image can be queued before the current one is saved.
- **Errors stop the chain.** If one image fails, no further prompt is queued — restart by pressing Queue.
- **`total` must be correct.** With `total = 0` the folder is counted at each run; if files are added mid-run the count changes.
- **One prompt per image** also means one entry per image in the history/sidebar — expect a long queue.
- Requires ComfyUI's `PromptServer`; not usable in a headless API-only setup that bypasses the server queue.

---

## License

MIT — see [LICENSE](LICENSE).

---

# 中文说明

**逐张队列**：把「一个 prompt 里用 for 循环跑完整个文件夹」改成「**一张图一个 prompt**」。每张图跑完后，本节点自动把同一个工作流以 `start_index + 1` 再排进队列，于是两张图之间 ComfyUI 会做常规清理——上一张的中间张量（VAE 分块、放大模型输出、缓存）被释放，而模型保持常驻显存，不再反复加载/卸载。

## 为什么需要

用 for 循环节点批量跑文件夹时，**整批图的所有中间结果会同时驻留**。24G 卡上处理 20 张 4K 放大，显存会直接爆，或者大量时间浪费在张量搬运上。拆成一张图一个 prompt 就没有这个问题，但 ComfyUI 本身没有"排队下一张"的能力，这个节点补上这一块。

## 参数

| 参数 | 说明 |
|---|---|
| `images` | **必连**，从 `SaveImage.images` 接过来，保证"当前这张存完"才执行本节点 |
| `start_index` | 当前处理到第几张（初始 0） |
| `total` | 总张数；填 `0` 则按 `directory` 自动统计 |
| `enable` | 关掉则完全不排队 |
| `directory`（可选） | 文件夹路径，用于自动统计张数 |
| `loader_node_id`（可选） | 指定要改 `start_index` 的加载节点 id；留空则自动查找类名以 `LoadImageListFromDir` 开头的节点 |

## 实现方式

取到当前正在执行的 **API 格式 prompt** → 深拷贝 → 把所有文件夹加载节点的 `start_index` 改成 `start_index + 1`（本节点自己也一起改，让链条能延续）→ 重新收集输出节点 → 通过 `PromptServer` 的事件循环把新任务放进队列。

全程 `try/except` 包裹，**任何异常都不会导致当前这张图失败**，最多是后续不再排队。

## 已知限制

- **种子会被冻结**：排队的 prompt 是"刚跑完的那份"的副本，随机化控件的值已经是具体数字，所以整批图会共用同一个种子。需要每张不同的话，请在图内用 `start_index` 推算种子。
- **本节点必须最后执行**：`images` 要接 `SaveImage` 的输出，否则可能这张还没存完就排了下一张。
- **出错即停**：某张失败后不会再排队。
- **`total` 要填准**：填 0 会每次重新统计文件数。
- 依赖 ComfyUI 的 `PromptServer`，纯 API 直连、绕过服务器队列的用法不适用。
