---
type: llm
focus: trace
weight: 1
---

核实步骤启动了新的 subagent（不是复用审查用的 agent），给核实者的 prompt 只有发现的位置和问题陈述，没有审查者的证据或推理。
