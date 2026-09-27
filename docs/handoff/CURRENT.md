# 中断接续点

更新：2026-09-28，当前任务进行中。每轮先读本文件、MEMORY_PROJECT、STATUS_PROJECT，并检查 Git，不能仅根据此文件认定代码已经完成。

目标：已确认 Planner 方案 → Developer 多文件提案 → 人审 → 隔离项目验证。

基线：2344f2a。B 阶段已有规划/审批接口、React 预览；规划 run 永远禁止直接执行。C 阶段计划新增 project_generation.py，沿用 artifact/run，不放宽单文件 Patches。

预计实现：生成独立草稿 run，模型调用一次；代码仍只存 artifact。人工批准后在临时目录准备整套文件，创建隔离执行 run，再跑 harness。审批引用用于查找已创建的执行 run，避免重复批准重复执行。

尚未验证；不能宣称多文件已完成。不要恢复或重复调用未知结果的付费请求。密钥只能在服务内存中，不写入接续记录。

接续命令：git status --short；.venv\Scripts\python.exe -m unittest discover -s tests；npm --prefix frontend test；npm --prefix frontend run build。

完成后必须更新本文件为准确的完成状态/剩余事项，提交独立增量，并生成 progress 报告与下一阶段计划。
