# 桐弦声检故障判读册

GaussianNB与分层交叉验证、混淆矩阵和后验解释。为桐弦研究协作组的12位分析与业务人员提供从样本核对、独立worker计算到批准快照交付的完整工作流。

## 真实业务场景

合成声学特征→故障类型判读→复检批注；真实训练/验证分离、训练内标准化、类先验展示。团队原先将 `acoustic-bayes-202611-01.csv` 复制到多份人工表格，复核时再逐项对照。三个痛点是口径不可追溯、重复计算易错、业务说明与结果脱节。上游提供合成业务观察，系统承担研究和复核，下游业务人员读取批准快照。全部演示数据是固定随机种子生成的模拟数据。

## 业务模块

1. 业务总览：批次、样本、任务与批准交付账册。
2. 声检批次：创建/修改来源说明、查看样本、用真实业务字段追加记录；列表分页。
3. 分析配置：冻结来源、参数和全部样本，进入Redis队列。
4. 完整结果：真实算法摘要、专属诊断、全部CSV导出和可恢复失败态。
5. 复检批注：分析师草案→负责人批准→阅读者下载批准快照。
6. 拆检估算：按已完成研究的折外判读分层随机抽样，用拆检结论按入样概率倒数估算全批误判率；封账版本与更正分开留痕。
7. 审计：登录、来源修改、任务worker完成、业务说明批准、拆检计权与发布等事件。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 可重复执行，不覆盖人工修改
docker compose exec -T backend python -m scripts.seed_demo
```

访问 [前端](http://localhost:18245)；[API文档](http://localhost:19245/docs)。数据库仅演示用途映射 `17745`。首次准备依赖和初始化数据库需要等待健康状态：`docker compose ps`，五个服务均需healthy。

| 组件 | 技术与职责 |
|---|---|
| frontend | 原生 HTML / CSS / JS ES Modules，Nginx代理 `/api/` |
| backend | Python3.11 / FastAPI / SQLAlchemy / JWT |
| worker | 独立Python进程，Redis BLPOP队列，持久化结果/CSV/心跳 |
| db | MySQL8，命名卷持久化 |
| redis | Redis7，AOF命名卷 |

恰好五服务，均配置healthcheck；后端/worker依赖数据库与Redis健康，前端依赖后端健康；worker与后端共享 `artifacts` 命名卷。重置演示环境：`docker compose down -v --remove-orphans`，会删除本项目卷。

## 账号与权限

所有演示账号密码：`Research2026!`。

| 邮箱 | 角色 | 权限 |
|---|---|---|
| admin@acoustic-bayes.demo | admin | 全部录入/执行、批准业务说明 |
| analyst@acoustic-bayes.demo | analyst | 录入来源/样本、执行、提交说明，无批准权 |
| viewer@acoustic-bayes.demo | viewer | 只读、导出结果及已批准快照 |

时间在数据库/API中以UTC存储，总览与审计页面明确转换为Asia/Shanghai北京时间。

JWT有效期8小时；PBKDF2盐化哈希保存密码；角色每请求从数据库读取。访客写操作403，匿名访问业务数据401，重复样本409，非法业务字段422，重复批准409。每客户端/路径写请求90次/分钟，超限429。生产使用需修改JWT及演示数据库凭据。

## 实体与字段

AcousticBatch / AcousticSample / ClassifierRun / InspectionNote 是核心实体；另有User和Audit。数据库实际表：`acoustic_batches` → `acoustic_samples` → `classifier_runs` → `inspection_notes`。样本以固定字段JSON保存，后端按以下字段严格验证，不接受未知字段替代必填值：均方根振幅（`rms`）、频谱质心 Hz（`centroid`）、峭度（`kurtosis`）、复核类型（`label`）。

实体贯穿 `backend/src/models`、`schemas`、`services`、`controllers`、`routes`，以及 `frontend/src/api/research.js`、`stores/research.js` 和 `pages`。`Source.key` 与 `(Record.source_id, reference)` 为自然键；来源改名不会修改已冻结任务输入。业务说明在创建时保存结果快照，批准后从该快照导出。

拆检估算另有四张表：`teardown_plans`（计划：来源研究、冻结校验和、seed、各层全量/抽取数/入样概率/抽中名单）、`teardown_receipts`（拆检回件：样本、结论、收到时间）、`teardown_editions`（封账版本：收件截止、冻结快照、估计量）、`teardown_strata`（版本内各层对账与计权）。`(plan_id, version_no)` 与 `(edition_id, stratum_key)` 唯一；同一计划同一收件时刻不复写回件，更正请用新的收到时间。

## 拆检抽样与误判率口径

现场会优先拆检疑似故障件，直接用拆检样本算误判率有选择偏差，不能当全批结论。正确口径分四步，全部位于 `backend/src/services/teardown.py`：

1. **按已完成研究冻结的折外判读分层。** 计划只能引用 `completed` 任务的 `result.rows`，按折外**预测**类型分层；层内无放回随机抽样，seed 固定，抽样结果与入样概率 `π_h = n_h / N_h` 在计划创建时锁定，回件录入不得改变名单。
2. **先锁定各层抽取数和入样概率。** 允许每层不同抽取数（`sample_sizes`）或统一 `per_stratum_sample`；抽取数超出层内全量、层名不在折外判读中、全部抽取数为 0 都拒绝。每个版本按计划复制一份各层名单，后续计划变更不影响既有版本。
3. **用拆检结论按入样概率倒数估算。** 误判指拆检结论与折外判读层不一致。层内计权误判数 `m_h / π_h`，全批误判数 `Σ_h m_h / π_h`，误判率再除以全批样本数 `N`。页面同时展示未经计权的拆检样本误判率作对照，并标注它不能直接当成全批结论。
4. **缺回件或有零概率层时不报数。** 任一层有未回件或 `π_h = 0` 时，`reportable` 为 false，全批估计与误判率均为 `null`，接口和页面只列缺口，不给出替代数字。

**封账与更正。** 封账按**收件截止时刻**取值：`received_at <= cutoff` 的最新一条回件进入本版，晚到或截止后的记录不进入本版，留给下一版。版本号递增且收件截止必须晚于上一版，避免新旧版本口径倒挂。草稿版本在再次计权时可采纳截止前的更正；已发布版本固定发布时的冻结快照，之后补录的倒签记录不会改写旧版，也不能重算或重发。

**中断与重试。** 分层计权逐层覆盖写入并持久化，每层 `weighted_misjudged` 是覆盖赋值而不是累加；中断后重跑同一版本不会重复加权，必要时可把单层重置为 `pending` 再恢复，结果与一次跑完一致。

**发布对账。** 发布前按本版冻结快照重算每层并逐项对账（全量、抽取数、入样概率、回件、缺件、误判、计权值）；任何一层不一致返回 409 并要求重新计权，所有层齐备才写入 `published`。页面提供“按当时拆检结论复算”只读预览，展示该版在冻结快照下的估计以及与已存计权是否一致。

## 算法与口径

GaussianNB与分层交叉验证、混淆矩阵和后验解释，全部计算位于 `backend/src/services/algorithm.py`，前端不拟合模型、不生成模拟结果。输入是冻结业务观察及参数，输出summary、专属诊断、逐行计算明细、seed与输入SHA256。CSV包含全部结果行；页面仅预览前30行。参数：

- `folds`：分层交叉验证折数，默认 `3`。
- `smoothing`：方差平滑系数，默认 `1e-09`。

分层交叉验证，每折只在训练行拟合均值与方差；后验概率是模型分布，不是检修结论。

失败态：业务字段缺失/非有限数值、参数越界、样本不足、退化特征或结构错误均明确拒绝或将任务标failed，错误消息保存在任务中。可回到来源或配置修正后重新执行，历史失败记录保留。

已知边界：独立同分布、特征条件独立和高斯类条件是假设；后验未校准。演示数据刻意有分离度，准确率不代表真实设备效果。使用分层折外验证，不提供外部最终测试集。

## 演示数据与一天剧本

固定seed=42，3个来源 ` TX-202611-01/02/03`，每来源240条，共720条业务记录；三个账号和首轮种子任务。自然键检查后仅插入缺失数据，连跑seed不重复、不覆盖用户修改。`SEED_DEMO=true` 时启动自动执行；失败记录日志但不阻塞服务。首轮任务同样由独立worker真实计算。

1. 09:00 负责人查看三份来源和720条样本。
2. 09:10 分析师选择 `TX-202611-01 秋季常规组`，核对 `acoustic-bayes-202611-01.csv`。
3. 09:15 在“声检批次”核对均方根振幅（`rms`）、频谱质心 Hz（`centroid`）、峭度（`kurtosis`）、复核类型（`label`），追加编号 `TX-NEW-001` 的样本。
4. 09:20 新建“十一月复核研究”，按默认参数冻结241条输入。
5. 09:21 独立worker消费Redis队列，返回实算结果与输入校验和。
6. 09:25 分析师查看专属诊断及逐行结果，并下载CSV复算。
7. 09:30 提交“十一月业务复核意见”，负责人核对并批准。
8. 09:40 viewer下载批准快照，查看审计中的worker完成与批准事件。
9. 10:00 分析师按首轮研究的折外判读分层建立“十一月拆检复核”计划，锁定各层抽取数与入样概率。
10. 11:00 现场拆检回件按收到时间录入；负责人以收件截止封账第1版，各层有缺件时页面明确“暂不报数”。
11. 次日 09:00 补齐回件后重新计权，各层对账齐，负责人发布第2版；截止后才到的记录留给第3版。查看器可用“按当时拆检结论复算”复核旧版。

## 目录与本地开发

```text
frontend/src/api, stores, types, components/common, hooks, pages, router, utils, constants
backend/src/routes, controllers, services, models, schemas, middlewares, utils, constants, config
backend/scripts/seed_demo.py
backend/migrations/001_initial.sql
```

本地后端：Python3.11虚拟环境安装 `backend/requirements.txt`，设置 `DATABASE_URL`、`REDIS_URL`、`ARTIFACT_DIR`，在backend目录运行 `uvicorn src.main:app --reload`；另开终端运行 `python -m src.worker`。原生前端无需编译，推荐以Compose Nginx访问，保证 `/api/` 代理。前端为Google Fonts双字体、oklch色板，离线字体回退serif；1280/768/390响应布局。

| 环境变量 | 默认与说明 |
|---|---|
| COMPOSE_PROJECT_NAME | `acoustic-bayes`，隔离容器/网络/命名卷 |
| FRONTEND_PORT / BACKEND_PORT / DB_PORT | `18245 / 19245 / 17745` |
| SEED_DEMO | `true` |
| JWT_SECRET | `.env.example`演示值，部署前修改 |
| DATABASE_URL / REDIS_URL | Compose内部连接，定义在compose的env锚点 |
| ARTIFACT_DIR | `/artifacts`，后端worker共享 |

共享枚举：`backend/src/constants/enums.py` 与 `frontend/src/constants/enums.js` 都定义 admin/analyst/viewer、queued/running/completed/failed、draft/approved；拆检版本用 draft/published，各层计权用 pending/computed/blocked。后端service检查对应字符串；README为全量值说明。共享前端组件table/status/chart/business跨总览、结果、配置与复核复用；actions hook和format utils负责交互/格式。后端errors与rate_limit是两个中间件。

测试及截图在批根 `test-artifacts/gb-0041/`，不混入源码。独立算法oracle、HTTP完整流程和真实Playwright浏览器流程见该目录 `TESTING.md`。License：MIT。
