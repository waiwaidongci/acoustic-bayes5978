# gb-0041 测试记录

## 拆检估算模块（2026-11-18）

`backend/test_teardown_flow.py` 用 SQLite + FastAPI TestClient 跑通拆检模块全流程，并用 `backend/oracle_teardown.py` 这个不导入业务代码的独立 oracle 对照计权结果。33 项断言全部通过。

```bash
cd backend
pip install -r requirements.txt httpx
python ../test-artifacts/gb-0041/backend/test_teardown_flow.py
```

覆盖点：

- 权限：viewer/analyst 建计划、发布的结果码。
- 抽样锁定：折外预测分层、各层全量/抽取数/入样概率、抽取名单固定；超抽、错层、全零抽取、未知字段拒绝。
- 回件：非抽中样本、无时区时间、非研究类型结论拒绝；重复提交幂等 `skipped`。
- 估计：与 oracle 和手工 `Σ m_h/π_h` 对齐；计权率与朴素样本率不同（体现选择偏差校正）。
- 不报数：缺回件、零概率层均 `reportable=false`、`estimated_rate=null`、逐层列阻断，且不可发布。
- 封账/更正：收件截止必须递增；截止前更正可被草稿再次计权采纳；截止后记录计入下一版。
- 中断恢复：逐层覆盖写入，重试与中断恢复后总量不变。
- 发布对账：逐层重算对账后才 published；重复发布与已发布重算均 409；旧版可按冻结快照复算且 `matches_stored`。
- 审计：计划、回件、版本、计权、发布均写审计事件。

## 未覆盖

本环境无 Docker、MySQL 与浏览器，未跑 Compose 五服务联调与真实 Playwright 浏览器流程；前端仅用 Node 语法检查与打桩渲染断言。MySQL 下 `with_for_update` 并发封账路径与真实 Redis worker 未在本机验证。
