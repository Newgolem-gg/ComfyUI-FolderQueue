# -*- coding: utf-8 -*-
"""
FolderQueueNext —— 逐张队列节点

用途：
    把「一个 prompt 里 for 循环处理整个文件夹」改成「一张图一个 prompt」。
    每张图执行完后，本节点自动把同一个工作流以 start_index+1 再排入队列，
    于是 ComfyUI 在执行两个 prompt 之间会释放上一张图的中间张量（缓存、VAE 分块、
    放大模型输出等），内存/显存不会随张数线性上涨，也不会再去反复卸载/重载模型。

用法：
    1. 把本目录整体放到 ComfyUI/custom_nodes/ 下，重启 ComfyUI。
    2. 工作流里删掉 easy forLoopStart / easy forLoopEnd / DocKr 清理节点，
       把 LoadImageListFromDir 的 start_index 改回普通数值（0）。
    3. 添加“逐张队列 / Folder Queue Next”节点：
          images      <- SaveImage 的 images 输出（保证一张图存完才执行本节点）
          start_index =  0
          total       =  图片张数（填 0 则按 directory 自动统计）
          directory   =  （可选）接文件夹路径，total=0 时用它统计张数
    4. 点一次 Queue 即可。控制台会打印 "[FolderQueue] 已排队 x/y"。
"""

import copy
import os
import time
import uuid

import nodes
from server import PromptServer

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


def _count_images(directory: str) -> int:
    try:
        if not directory or not os.path.isdir(directory):
            return 0
        return sum(1 for f in os.listdir(directory) if f.lower().endswith(IMAGE_EXTS))
    except Exception:
        return 0


class FolderQueueNext:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "start_index": ("INT", {"default": 0, "min": 0, "step": 1}),
                "total": ("INT", {"default": 0, "min": 0, "step": 1}),
                "enable": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "directory": ("STRING", {"default": ""}),
                "loader_node_id": ("STRING", {"default": ""}),
            },
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
                "extra_pnginfo": "EXTRA_PNGINFO",
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "queue_next"
    OUTPUT_NODE = True
    CATEGORY = "utils"
    DESCRIPTION = "Enqueue the same workflow with start_index+1 after each image, so every image runs as its own prompt."

    def queue_next(self, images, start_index=0, total=0, enable=True,
                   directory="", loader_node_id="", prompt=None,
                   unique_id=None, extra_pnginfo=None):
        try:
            if not enable:
                print("[FolderQueue] 未启用，跳过自动排队")
                return {"ui": {"text": ["未启用"]}}

            if prompt is None:
                print("[FolderQueue] 拿不到当前 prompt，无法排队")
                return {"ui": {"text": ["无 prompt"]}}

            total = int(total)
            if total <= 0:
                total = _count_images(directory)
            if total <= 0:
                print("[FolderQueue] total<=0 且无法从 directory 统计张数，跳过自动排队")
                return {"ui": {"text": ["张数未知"]}}

            nxt = int(start_index) + 1
            if nxt >= total:
                print(f"[FolderQueue] 全部完成：{total}/{total}")
                return {"ui": {"text": [f"完成 {total}/{total}"]}}

            new_prompt = copy.deepcopy(prompt)

            # 找到文件夹加载节点，把 start_index 改成下一张
            targets = []
            if loader_node_id and str(loader_node_id) in new_prompt:
                targets.append(str(loader_node_id))
            else:
                for nid, nd in new_prompt.items():
                    if str(nd.get("class_type", "")).startswith("LoadImageListFromDir"):
                        targets.append(nid)
            if not targets:
                print("[FolderQueue] 没找到 LoadImageListFromDir 节点，跳过自动排队")
                return {"ui": {"text": ["无加载节点"]}}

            for nid in targets:
                new_prompt[nid].setdefault("inputs", {})["start_index"] = nxt

            # 让下一个 prompt 里的本节点也带上新的 start_index
            me = new_prompt.get(str(unique_id))
            if me is not None:
                me.setdefault("inputs", {})["start_index"] = nxt

            # 输出节点列表（等价于 ComfyUI 的 validate_prompt 结果）
            output_ids = []
            for nid, nd in new_prompt.items():
                cls = nodes.NODE_CLASS_MAPPINGS.get(nd.get("class_type"))
                if getattr(cls, "OUTPUT_NODE", False) is True:
                    output_ids.append(nid)

            server = PromptServer.instance
            if server is None:
                print("[FolderQueue] PromptServer 不可用，跳过自动排队")
                return {"ui": {"text": ["无服务"]}}

            extra_data = {}
            if extra_pnginfo:
                extra_data["extra_pnginfo"] = extra_pnginfo
            client_id = getattr(server, "client_id", None)
            if client_id:
                extra_data["client_id"] = client_id
            extra_data["create_time"] = int(time.time() * 1000)

            number = server.number
            server.number += 1
            new_prompt_id = str(uuid.uuid4())
            item = (number, new_prompt_id, new_prompt, extra_data, output_ids, {})

            def _put():
                server.prompt_queue.put(item)

            # 在服务端事件循环里入队，避免跨线程问题
            loop = getattr(server, "loop", None)
            if loop is not None:
                loop.call_soon_threadsafe(_put)
            else:
                _put()

            print(f"[FolderQueue] 已排队下一张：{nxt + 1}/{total}（start_index={nxt}）")
            return {"ui": {"text": [f"已排队 {nxt + 1}/{total}"]}}

        except Exception as e:
            # 任何异常都不应该让当前这张图失败
            print(f"[FolderQueue] 自动排队失败：{type(e).__name__}: {e}")
            return {"ui": {"text": [f"排队失败: {type(e).__name__}"]}}


NODE_CLASS_MAPPINGS = {
    "FolderQueueNext": FolderQueueNext,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FolderQueueNext": "逐张队列 / Folder Queue Next",
}
