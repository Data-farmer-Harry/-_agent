# Interview Playbook

这份文档不是给代码看的，是给你在面试里“讲项目”时用的。

目标不是把项目说得很神，而是把它讲得真实、清楚、有工程味。

## 1. 30 秒版本

“我做了一个面向材料研究场景的 Agent 项目。它不是简单把 LLM 接进一个接口，而是做了 workspace、route、plan、tool、trace、artifact 这一整套运行时。当前已经打通相图生成和相图截图重建两条链路，并给未来的 LAMMPS agent 预留了同一套扩展接口。项目里一个关键决策是 deterministic fallback，也就是模型失效时系统仍能稳定交付结果页，而不是整条链路直接崩掉。” 

## 2. 2 分钟版本

可以按这个顺序讲：

1. 业务场景
   面向科研组内成员，目标不是追求一个万能聊天机器人，而是把常见材料任务收敛成可运行的工具工作流。

2. 为什么是 Agent
   项目不是单接口，而是先把任务路由到 workspace，再变成 plan，再由 runtime 调 tool，并记录 trace 和 artifact。

3. 当前两条完整链路
   一条是相图生成：codegen -> execute -> repair -> fallback。
   一条是截图重建：image parse -> deterministic render。

4. 最重要的工程取舍
   我没有让模型直接负责最终交付，而是让它只负责提升质量上限；底线由 deterministic fallback 守住。

5. 可扩展性
   我把未来的 LAMMPS 接口先做成 workspace stub，这样后续新增模拟工具时，不需要重做前后端协议。

## 3. 推荐 Demo Path

### Demo A：相图生成

讲述重点：

- 输入材料体系和参数
- 展示 route、selected tool 和 plan steps
- 展示结果 HTML、trace、生成代码和日志
- 如果被问“模型不稳定怎么办”，顺势讲 fallback

### Demo B：图片识别

讲述重点：

- 上传相图截图
- 手动校准 X/Y 轴
- 展示原图和生成页面对照
- 强调“模型先出结构化 spec，再渲染页面”

### Demo C：LAMMPS 规划位

讲述重点：

- catalog 里已经能看到 `lammps` workspace
- 现在不是伪造模拟能力，而是诚实地只暴露 stub-ready 状态
- 后续会把 command router 后面接到真正的 codegen / execute / repair

## 4. 最值得讲的亮点

### 亮点 1：Agent 是 runtime，不是 prompt

很多项目把 Agent 等同于“让模型多想几步”。这个项目更强调运行时结构：

- route
- plan
- tool registry
- trace
- artifact

这会让面试官更容易把你归类到“懂系统设计”，而不是“只会调 prompt”。

### 亮点 2：deterministic fallback 是工程化关键

这类项目最容易被质疑的点是“不稳定”。你可以直接回答：

“我没有把系统成功完全押在模型质量上。相图链路有 placeholder fallback，图片链路有 manual calibrated fallback，所以系统即使在模型波动时也不会完全失去交付能力。”

### 亮点 3：多模态模块是可控设计

这里不要说“我做了自动识别相图并完全重建”。更好的讲法是：

“我把多模态模型用于结构化理解，把页面渲染放回确定性代码，这样更可测、更可解释，也更适合后续让用户手工修正。”

## 5. 如果面试官问：为什么不直接让大模型输出 HTML

推荐回答：

“因为 HTML、可视化布局、前端稳定性和业务语义是不同层面的问题。让模型直接输出最终页面，调通一次可能很快，但很难保证运行稳定、可维护和可测试。我这里把它拆成结构化 spec 和确定性 render，整体更像一个工程系统，而不是一次性的 demo prompt。”

## 6. 如果面试官问：为什么现在的 planner 不是开放式自主规划

推荐回答：

“这是一个刻意的边界。当前任务域比较明确，所以我先用规则型 planner 把 route 和 tool chain 固定下来，换来更高的可解释性和可测试性。等 workspace 和 tool 更多以后，再决定是否引入更开放的 planning。” 

## 7. 如果面试官问：LAMMPS 还没接完，为什么要放进项目里

推荐回答：

“因为这个项目真正想验证的是 Agent runtime 的扩展能力。LAMMPS 不是额外加一个按钮，而是验证现有 workspace、catalog、plan、artifact 这套抽象能否承接下一类工具域。现在先把 stub 铺好，是为了保证下一步扩展是连续演进，而不是推倒重来。” 

## 8. 边界怎么讲才不吃亏

建议你主动把这几个边界讲出来：

- 相图结果当前更偏工程演示，不是严格热力学求解平台
- planner 当前是规则型 planner
- 图片识别更注重稳健，不是全自动高精度重建
- LAMMPS 现在还没有真实执行

主动说边界，通常比被面试官指出边界更有说服力。

## 9. 你可以用的一句总结

“这个项目的核心不是做一个会聊天的材料助手，而是把不稳定的模型能力收敛成一个可运行、可追踪、可扩展的领域 Agent 系统。”
