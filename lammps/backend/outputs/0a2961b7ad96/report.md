# MD Agent Run Report

## 用户目标
Run an equilibration for Cu at 500K for 5000 steps.

## 归一化参数
{'material': 'Cu', 'potential_family': 'eam', 'task_type': 'equilibration', 'temperature': 500, 'steps': 5000}

## 执行模式
mock

## LAMMPS 配置
command=
potentials_dir=

## 上传附件
- Cu_custom.eam | category=potential | conversation_mode=extracted | supported=True | applied=yes | context_only=False
- snapshot.png | category=image | conversation_mode=multimodal | supported=False | applied=no | context_only=True

## 关键热力学结果
{'final_temp': 500.0, 'final_pe': -3.26, 'final_etotal': 9.24, 'max_press': 140.0}

## 扩散轨迹图
supported_task=False
backend=not checked
generated=False
reason=mock mode: diffusion trajectory skipped
animation_path=
video_path=


## 失败信息或风险提示
Mock mode forced by USE_MOCK=true.

## 后续建议
- 若当前为 mock 模式，请配置 `LAMMPS_CMD` 与 `POTENTIALS_DIR` 后重试。
- 若需要更多体系，请扩展 `generate_lammps_in.py` 中的模板和材料映射。
