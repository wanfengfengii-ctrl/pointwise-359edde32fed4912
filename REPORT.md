# 校准换版超阈时长：独立质量验证报告

本报告只覆盖本轮新增的独立判定与质量验证；生产源码 `calibration.py`、
`exposure.py`、`pipeline.py`、`samples.py`、`sample.json`、`checks.py`
保持冻结，未做任何功能修改。结论可由 `python3 -I qa.py verify` 重算，
证据落在 `evidence.json`，失败输入保留在 `qa_artifacts/failures/`。

QA-BASELINE:PASS
QA-SELF-ORACLE:PASS
QA-RULE-UNITS:PASS
QA-BOUNDARY:PASS
QA-SEEDED:PASS
QA-STRESS:PASS
QA-M1:DETECTED
QA-M2:DETECTED

## 1. 独立数学依据（不调用生产计算）

每段输入十进制原文按精确有理数 `Fraction` 解释。设段长 `h=e-s`、局部时间
`u=t-s`，则

- 原始采样 `x(u)=r0+(r1-r0)u/h`（线性）；
- 校准 `P(x)=a0+a1 x+a2 x^2+a3 x^3`；
- 物理曲线减阈值 `g(u)=P(x(u))-threshold` 是 `u` 的次数不超过 3 的
  有理系数多项式，系数由分数多项式乘法精确展开，无浮点参与。

严格超阈时长是集合 `{u∈[0,h] : g(u)>0}` 的勒贝格测度；相切单点、边界交点
单点不贡献测度，`g≡0`（恒等阈值）贡献零。求根不使用生产的浮点二分，也不
使用第三方求解器，而是：

1. `gcd(g, g')` 去重，得到与 `g` 同根集合的无重根多项式；
2. 构造 Sturm 链，用符号变化数在开区间精确计数互异实根；
3. 有理中点二分隔离全部根：中点恰为根时得到零宽有理闭区间（可手算的有理根
   全部精确命中），无理根被宽度 ≤1e-12 的有理闭区间夹逼；
4. 在根之间空隙取有理样本点精确判号，正区间长度按分数求和，根的不确定性
   线性传播，输出包含真值的闭区间 `[L,U]`，200 段总宽不超过 6e-10 秒，
   满足契约总宽 ≤1e-6 秒。

时间网格、浮点互比均不参与正确性判据；重复根、相切、边界交点、恒等阈值、
下降采样、换校准与时间原点平移均由同一套精确逻辑覆盖。

## 2. 交接曲线核对（局部时间语义）

独立展开的局部系数 `b0..b3`（见 `oracle.local_coefficients`）与生产输出
逐系数核对：把每个生产 float 按其二进制实际值解释为有理数 `c_k`，校验
`|(c_k-b_k)h^k| ≤ 1e-10·max(1,|b_k h^k|)`，`k=0..3`；同时核对时间原文、
条数、顺序与有效域 `h`。该容差只用于交接，不替代时长判据。所有验证请求的
交接核对均通过（明细见 `evidence.json` 的 `handoff` 字段）。

## 3. 判定规则

生产 float 按二进制实际值解释为 `R`，独立区间 `[L,U]`：

- `dmax=max(|R-L|,|R-U|) ≤ 1e-5`：通过；
- `dmin=max(L-R,R-U,0) > 1e-5`：阻断；
- 其余：未判定，不计通过。

非有限结果、错域、交接越容差同样阻断；阻断优先于未判定。一个请求全部交接、
每段与总结果均通过才算通过。三分支边界（含 `dmax=1e-5` 通过、
`dmin=1e-5` 未判定、越带阻断、NaN 阻断）与交接容差带内/越界的合成单元
证据见 `evidence.json` 的 `rule_unit_checks`，标记 QA-RULE-UNITS:PASS。

## 4. 判定器自身的手算核验（独立背书，非与生产互证）

| 标记 | 案例 | 独立精确期望 |
|---|---|---|
| QA-CASE:B01-normal-two-segment:PASS | 两段 u 与 2-u，阈值 1 | 每段 1，总 2（零宽） |
| QA-CASE:B02-constant:PASS | 常量 3，阈值 2，h=4 | 4（零宽） |
| QA-CASE:B03-linear:PASS | 线性 u，阈值 1 | 1（有理根，零宽） |
| QA-CASE:B04-descending:PASS | 下降采样 1-u，阈值 0 | 1（零宽） |
| QA-CASE:B05-cubic-rational:PASS | u³，阈值 1 | 1（根 u=1，零宽） |
| QA-CASE:B06-tangent:PASS | 相切 (u-1)²，阈值 0 | 严格 >0 全段 2，切点不扣 |
| QA-CASE:B07-repeated-root:PASS | 三重根 (u-1)³ | u>1 给 1，重复根正确穿越 |
| QA-CASE:B08-boundary-crossing:PASS | 交点恰在 u=0 | 单点不扣，时长 2 |
| QA-CASE:B09-identity-threshold:PASS | 整段恒等阈值 | 0 |
| QA-CASE:B10-three-roots:PASS | (u-1)(u-2)(u-3)，[0,4] | 正区 (1,2)+(3,4)=2，三次穿越 |
| QA-CASE:B11-origin-shift:PASS | 原点平移到 100 | 语义不变，各段 1 |
| QA-CASE:B12-calibration-switch:PASS | 相邻段换校准 | 段时长 1 与 2，总 3 |
| QA-CASE:B13-near-roots:PASS | 相距 2e-6 的近根窄暴露带 | Decimal 50 位独立真值 0.000002 被区间包含 |
| QA-CASE:B14-irrational-quadratic:PASS | u²-2，无理根 √2 | Decimal 50 位真值 2-√2 被区间包含 |

B13/B14 的真值用标准库 `decimal` 50 位独立计算，仅用于核验判定器区间包含
关系，不参与对生产的判定；其夹逼宽度记录在证据中。

## 5. 固定种子组合与 200 段压力

- 固定种子 `20261116, 777001, 424242, 909090, 31337`，每种子 6 组随机合法
  请求（共 30 组），请求全文、种子与组合号保存在 `evidence.json`，可重放；
- 压力负载为种子 `5150200` 生成的 200 段合法请求，全文保存在
  `qa_artifacts/stress_request.json`；
- 判定统计（通过/阻断/未判定）与最终区间宽度见证据与下方资源块。

QA-RESOURCE-BLOCK:START
（本块由 `qa.py verify` 每次重算后自动刷新，不以静态历史数字为门槛）
- 压力负载：200 段，种子 5150200
- 贯通验证耗时（含生产分析与独立判定，秒）：3.330298
- tracemalloc 峰值内存（字节）：995783
- 判定器总区间宽度（秒）：235861/2199023255552000
- 单段最大区间宽度（秒）：3291/1099511627776000
- 边界判定计数（通过/阻断/未判定）：{'pass': 14, 'block': 0, 'undetermined': 0}
- 固定种子判定计数：{'pass': 30, 'block': 0, 'undetermined': 0}
- 压力判定计数：{'pass': 1, 'block': 0, 'undetermined': 0}
QA-RESOURCE-BLOCK:END

## 6. 变异见证（异常退出不算检出）

变异在独立进程中经 `mrunner.py` 对运行时函数打补丁，实际进入
`pipeline.analyze` 的校准→暴露调用链，不改冻结文件；导入失败、异常或仅改
标签均判未检出。

- QA-M1:DETECTED —— 局部时间坐标误用：把局部 `u` 系数误当绝对时间 `t`
  系数求值再展回局部格式。合法单段（10..12，u，阈值 1）下生产基线时长 1
  秒、系数 `(0,1,0,0)`；变异给出系数 `(10,1,0,0)`、时长 2 秒，均为有限
  有效结果，独立判据 `dmin=dmax=1` 秒，阻断。
- QA-M2:DETECTED —— 漏掉同段两次穿越：时长统计每个单调段只保留第一个
  割点。合法输入 `(u-1)(u-2)(u-3)`、`u∈[0,4]` 有三次穿越、真值 2 秒；
  变异漏计 `(3,4)` 暴露带，输出有限值 0 秒，独立判据 `dmin=dmax=2` 秒，
  阻断。基线（未变异）同输入通过，证明差异来自变异本身。

## 7. 发布意见

本轮全部边界案例、固定种子组合与 200 段压力的交接和时长判定均通过，未出现
阻断或未判定；两类业务变异均被独立判据阻断检出；原 `checks.py` 回归原样
通过。当前重算结论为 **可上线通过**（以 `evidence.json` 的
`overall_verdict=pass` 与 verify 退出码 0 为准）。

若后续重算出现阻断，`qa.py verify` 退出码为 1 并保留失败输入，不得改生产
代码凑绿；仅未判定时退出码为 2。报告的发布意见标记随重算自动刷新：

QA-RELEASE-OPINION:PASS
（verify 交叉核对：overall=pass 时本标记必须为 PASS；block 时为 BLOCK；
undetermined 时为 UNDETERRED。）
