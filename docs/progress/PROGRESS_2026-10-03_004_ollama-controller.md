# Ollama 控制页与 GLM 本地验证

本轮优先纯本地，未调用云端。已将 glm-4.7-flash:latest 注册并设为默认模型，旧任务仍使用原快照。

## 可使用的功能

前端顶部/侧栏增加本地模型入口：安装列表、加载列表、磁盘大小、参数/量化、权重摘要、模型详情、模型内存/显存、加载上下文、默认切换、加载/释放与一次真实测速。详细原始计数可展开。后台使用固定回环 Ollama API，没有任意 shell，也不下载/删除模型。

项目顶部常驻阶段、模型、经过时间和最近完成调用的实际速率。后台完成与 Gate 独立显示，失败不能显示为验证通过。速率来自真实 eval_count/eval_duration，不是估算；当前非流式，响应前不显示本次瞬时速率。

代码分工：infrastructure/ollama.py 管理固定本地接口；llm.py 归一化推理计数；RoleRuntime/单文件生成保存计数；Console复用既有worker/Jobs暴露控制动作与阶段；React OllamaPanel/LiveStatus呈现结果。没有新状态机或依赖框架。

## 真实本地结果

- 检测到4个模型，包括GLM、两个Qwen和Gemma；Ollama版本0.35.0。
- GLM磁盘约17.7 GiB；一次加载观测模型显存约13.68 GiB、上下文16384。磁盘文件大小不等于显存使用。
- GLM实际生成Clamp函数，首次编译失败（把error接口写成结构体），依据真实诊断进行一次明确的本地修复后，Go test/vet/格式与Gate通过。最终run ed43f7e3bfc14753af577a0fa3f2148f；首次失败bec9cac2c51843afbdf6aeb94696eb86。
- 修复调用输入669、输出216 tokens，实际54.87 tokens/s，总耗时约4.45秒；首次调用54.16 tokens/s。证据 .masa/glm-clamp-acceptance.json。
- 多文件随机数与整数求和尚未稳定通过：Tester重复检查、源码重复/截断、测试语法和未使用变量/导入。整数求和最终766363c9e25942978e6f11062e452788的Gate失败，后台流程结束不等于验证成功。未弱化断言，也未使用云端掩盖。

原生Tester改用按工具名称组织的对象，再归一化为既有列表；仍验证白名单、引用与测试案例。复杂tuple schema在当前Ollama不兼容，已移除。源码使用普通JSON，批准路径/模块/测试冻结仍严格校验。自动重试未知请求仍禁止，所有显式尝试保留旧记录。

前端10项与构建通过；后端全量验收数字见STATUS。浏览器工具无可用会话，视觉/点击未验收。

## 下一步

继续纯本地，多文件生成需要更小的文件/阶段输出预算、语法预检和明确失败恢复；不能只加大输出上限。控制器的下载/删除/自由终端暂缓。硬件监控只提供可选方案，无需本轮提权，见DESIGN_OPTIONAL_HARDWARE_MONITOR。复杂模型路由/API升级暂停推进。

重启MASA使用新版，入口和速率说明见USER_OLLAMA_CONTROLLER。真实成功代码可在 .masa/workspaces/ed43f7e3bfc14753af577a0fa3f2148f/solution.go 查看。
