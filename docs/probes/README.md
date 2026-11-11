# docs/probes —— 开发期格式探针

`.prproj` 的 XML 结构 Adobe 从未公开，这些脚本是逆向过程中用来「问文件本身」的小工具：只读工程、不解压到磁盘，把某个节点的上下文打出来看。

| 脚本 | 干什么 |
|---|---|
| `probe_media.py` | 列出所有 `<Media>` 的 Title / FilePath，核对「物理文件全集」 |
| `probe_bin_tree.py` ~ `probe_bin_tree4.py` | 逐版尝试走通素材箱树，第四次才定稿成现在的 `_walk_bins` |
| `probe_chain.py` | 打印某条目往下的引用链，用来确认它指到的是剪辑还是列定义 |
| `probe_break.py` | 找引用链在哪一层断掉（PR 26 里 `ClipProjectItem → MasterClip → Clips → Clip` 走到列定义就断） |

用法：

```bash
python docs/probes/probe_media.py "D:\media\project.prproj"
```

这些脚本不参与打包流程，也不是稳定 API，纯粹留作后来人排查格式问题的起点。
