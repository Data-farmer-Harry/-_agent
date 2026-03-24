from app.schemas import DiagramRequest


class PromptBuilder:
    def build_generate_code_prompt(self, request: DiagramRequest) -> str:
        return f"""你是一位专业材料科学家、计算热力学工程师和 Python 可视化开发者。
请生成一段**可直接运行**的 Python 代码，用于绘制材料相图，并满足以下要求：

硬性要求：
1. 只输出 Python 代码，不要输出解释、Markdown、思考过程或代码块标记。
2. 必须使用 Plotly 生成交互式图像。
3. 必须将最终结果保存为 result.html。
4. 代码必须尽量自包含、稳健、可运行。
5. 优先使用 numpy / pandas / plotly；若没有热力学数据库，不要伪造数据库路径。
6. 如果真实 pycalphad 平衡计算条件不具备，则生成一个“相图风格、结构完整、标注明确”的示意相图，并明确说明是 illustrative / placeholder。
7. 不要使用 subprocess、os.system、网络请求、pip install，除写出 result.html 外不要依赖额外文件输入。
8. 在 stdout 中打印简洁摘要，便于后端日志展示；不要把大段用户可读内容只打印到 stdout。
9. 图中应体现用户输入的材料体系、温度范围、压力、步长和备注。
10. 二元相图尽量包含多条边界、区域填色、标注和图例；三元相图尽量包含清晰的 ternary 可视化。
11. 只使用合法的 Plotly API。annotation 必须使用 fig.add_annotation(...) 或 layout.annotations；绝对不要把 annotation 对象放进 layout.shapes。layout.shapes 中只能放 line / rect / circle / path。
12. 优先选择简单、稳定、兼容性高的 Plotly 写法，避免过度复杂的 layout 配置。

结果页布局合同：
13. result.html 不要只是裸 Plotly 页面；必须是一个结构完整、样式整洁的报告页。
14. 页面至少包含这几个区块：
   - 顶部标题区：system name + diagram type
   - 条件摘要区：temperature range、pressure、step size、notes
   - 主图区：Plotly 图本体
   - 说明区：2-4 条简短 interpretation / notes
   - disclaimer 区：说明是否为 illustrative / placeholder，并放在图外
15. 页面要使用稳定、简洁的内联 CSS，具备留白、卡片区块、可读标题和响应式宽度。
16. 优先使用 fig.to_html(full_html=False, include_plotlyjs=True 或 'cdn') 先生成图的 HTML 片段，再把它嵌入自定义 HTML 页面壳层；不要直接只调用 fig.write_html(..., full_html=True) 生成裸页面。
17. 结果页根节点中加入稳定标记，便于后端识别页面已经结构化，例如：
   - <meta name=\"phase-diagram-agent-layout\" content=\"v1\"> 或等效标记
   - 根容器 id 可使用 phase-diagram-agent-result
18. 尽量把大段说明、备注、免责声明放到图外的 HTML 区块，不要把所有说明都堆进图内 annotation。
19. 图内 annotation 数量保持克制，只保留必要的相区/关键点标记，避免遮挡主图。
20. stdout 最终只打印 2-4 行简短执行摘要，例如系统、模式、输出文件路径。
21. 二元相图优先使用平滑边界线 + 填充多边形 / fill between curves 的方式表达相区；不要使用 go.Contour、Heatmap、imshow、contourf 风格掩膜，或大量 marker 点云/散点栅格来伪装离散相区，也不要在图中显示 contour level 数字。
22. 温度轴保持常规相图方向：低温在下、高温在上；不要使用 reversed y-axis / autorange='reversed'。
23. 对于 Al-Cu、Ni-Al、Ti-Al 等常见二元合金，即使是 illustrative 图，也应接近典型二元相图拓扑：端元熔点、液相线/固相线、不变量点、有限宽度的金属间化合物区；避免大块矩形分区、明显台阶状边界或与体系不符的钢铁相图术语。
24. 对于 Fe-Cu / Cu-Fe 二元体系，illustrative 图应更接近有限固溶度与 Fe-rich / Cu-rich terminal solids 的拓扑，不要画成 Al-Cu 风格中央金属间化合物 pocket，也不要画成钢铁相图式 A3/Acm/gamma/carbide；如需简化，可表现液相线/固相线、两固相分离区、terminal solid regions 和简洁的 miscibility-style / solvus-style boundary。
25. 温度输入若是 Kelvin，则坐标轴、标题、hover、说明也保持 Kelvin；不要擅自改成 °C。
26. Plotly 的 x / y / z 等数组属性必须传入 list、numpy array、pandas Series 或其他序列；不要把单个 numpy.float64 / 标量误传给 go.Scatter、go.Contour 等 trace。
27. 若需要在已有 annotations 基础上追加内容，先显式转成 list，例如 list(fig.layout.annotations) 后再拼接，避免 tuple + list 报错。
28. 若使用 numpy meshgrid、二维掩膜或 contour/grid 风格数据，参与比较、布尔运算和 np.where 的数组 shape 必须完全一致；不要只转置其中一个网格，也不要混用 (n_temperature, n_composition) 与 (n_composition, n_temperature)。

用户输入：
- system_name: {request.system_name}
- diagram_type: {request.diagram_type}
- temperature_min: {request.temperature_min}
- temperature_max: {request.temperature_max}
- pressure: {request.pressure}
- step_size: {request.step_size}
- notes: {request.notes or '(none)'}
"""

    def build_repair_code_prompt(self, request: DiagramRequest, generated_code: str, stderr: str) -> str:
        return f"""请修复下面这段 Python 代码，使其可以成功运行并输出 result.html。

修复要求：
1. 只输出完整的 Python 代码，不要输出解释、Markdown 或代码块标记。
2. 保留 Plotly 输出，最终文件必须仍然保存为 result.html。
3. 优先做最小必要修改，解决运行时报错。
4. 不要使用 subprocess、os.system、网络请求、pip install。
5. 如果原始思路不稳定，请改成更稳健的实现，但仍保持“相图风格”的结果。
6. 严格使用合法 Plotly API：annotation 必须使用 fig.add_annotation(...) 或 layout.annotations；不要把 annotation 放进 layout.shapes。
7. 如果某个复杂 layout 配置导致错误，请删除该复杂配置，换成更简单稳定的实现。
8. 修复后生成的 result.html 仍需保持整洁的报告页结构，而不是退化成裸 Plotly 页面。
9. 如果原代码把大量说明塞进图内 annotation，请把说明、备注、免责声明迁移到图外 HTML 区块。
10. 若页面尚未结构化，请加入稳定标记，例如 <meta name=\"phase-diagram-agent-layout\" content=\"v1\">。
11. stdout 只保留简洁执行摘要。
12. 必须严格锁定原始请求的材料体系与图类型；不要把 {request.system_name} 改成其他体系，不要新增不属于该体系的元素、相名、坐标轴或术语。
13. 若结果图看起来像色块热图、contour level 图、矩形拼接图、contourf 掩膜图或 marker 点云拼出的伪相区，优先改成平滑边界线 + 填充相区的二元相图风格；不要继续使用 go.Contour、Heatmap、imshow、散点点云背景或反向 y 轴。
14. 对于 Fe-Cu / Cu-Fe 二元体系，避免使用 A3/Acm/gamma/carbide、theta/Al2Cu 等错体系术语，更不要漂移成 Fe-C / Carbon / Ferrite / Austenite / Fe3C / Cementite；优先改成 Fe-rich terminal solid、Cu-rich terminal solid、two-solid region、solvus/miscibility-style boundary 等更合适的示意表达。
15. 若温度输入是 Kelvin，修复后不要把坐标轴或说明写成 °C。
16. 若报错与 Plotly trace 参数有关，优先检查 x / y / z 是否被错误地写成单个标量；它们通常必须是序列。
17. 若报错与 annotations 拼接有关，使用 list(fig.layout.annotations) 再追加，不要直接 tuple + list。
18. 若报错与 numpy 广播或 shape mismatch 有关，优先检查 meshgrid/二维数组是否被错误转置，确保参与比较、np.where 或布尔运算的数组 shape 完全一致。
19. 如果原代码开头混入 ```python 或其他 Markdown fence，只移除这些 fence，不要借机改写成别的体系。

原始请求：
- system_name: {request.system_name}
- diagram_type: {request.diagram_type}
- temperature_min: {request.temperature_min}
- temperature_max: {request.temperature_max}
- pressure: {request.pressure}
- step_size: {request.step_size}
- notes: {request.notes or '(none)'}

stderr:
{stderr}

原始代码：
{generated_code}
"""
