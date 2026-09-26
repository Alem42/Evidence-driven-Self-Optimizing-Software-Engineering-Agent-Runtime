# P1-01：受控修复与恢复

本阶段实现写入基础设施，不包含模型自动生成补丁。公开任务见 [Go Todo 契约](USER_GO_TODO_CONTRACT.md)。下一阶段 P1-02 增加 Go AST 和代码检索。

## 运行示例

在项目根目录运行以下命令。补丁生成脚本只生成已知示例答案，不调用模型，也不修改源仓库。

```powershell
.venv\Scripts\python.exe scripts/demo_patch.py
.venv\Scripts\python.exe -m masa run --repo examples/go-todo --goal "修复标题规范化与空白校验" --patch .cache/todo-patch.json
```

不带 `--patch` 运行同一示例，应得到 failed，因为公开测试刻意暴露原始缺陷。修复后得到 succeeded；2 次 scripted 调用，2 次工具预算消耗（1 次补丁、1 次 Go 检查）。一个 run 仍只选择一种 Go 检查；可使用 `--operation go_vet` 或 `--operation go_fmt_check` 新建独立运行。

```powershell
.venv\Scripts\python.exe -m masa report <run_id>
.venv\Scripts\python.exe -m masa resume <run_id>
```

补丁账本已写入但进程中断时，用 `resume` 恢复。它先逐文件核对哈希，再继续未完成部分，不重写已完成文件。出现前后哈希之外的内容时转为 `needs_attention`，停止自动恢复；检查冲突后从源仓库新建任务，不手动篡改数据库解除状态。

## JSON 补丁格式

```json
{
  "base_snapshot": "整个源文件清单的 snapshot 哈希",
  "files": [
    {
      "path": "todo/item.go",
      "before_sha256": "该文件原始字节的 SHA-256",
      "content": "完整的 UTF-8 新文件内容"
    }
  ]
}
```

这是全文件替换提案，不是 unified diff。补丁最多 20 个文件、新内容合计 1 MiB，副本仍受 32 MiB 限制。仅支持清单中已有的 Go 实现文件；拒绝测试文件、go.mod、新建/删除文件、路径别名和越界路径。

每个 run 只能在 `created` 且没有 attempt 时应用一份补丁；拒绝在已有验证结果后修改工作区。运行中修复需要后续 P1-04/P1-05 的新节点和版本语义，不能重置旧节点冒充新证据。

## 在前端查看

CLI 创建的运行会出现在同一状态目录的 React 控制台中。时间线含 `patch_intent` 和 `patch_committed`，引用可查看前后文件清单、补丁与新快照；运行报告也包含补丁记录。工具计数包含补丁，但「工具证据」列表目前只列 Go runner 调用。

本轮没有增加网页补丁编辑器，前端输入需求仍不会生成修复。旧服务若仍运行，重启后才能加载新报告逻辑。UI 的视觉/点击验收仍待可用浏览器。
