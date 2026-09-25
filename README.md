# 职业辐射剂量与异常事件

合并监测读数，比较历史剂量并管理超限调查、医学随访与报告期限。

## 模块结构

- `app.py`：参数解析、依赖组装和HTTP服务启动。
- `src/domain.py`：数据结构、错误、状态和基础校验。
- `src/rules.py`：状态机、角色矩阵、优先级、期限和关闭不变量。
- `src/repository.py`：SQLite建表、事务、版本控制和审计链。
- `src/service.py`：权限检查、用例编排、并发控制和审计。
- `src/http_api.py`：JSON路由和统一错误响应。
- `src/audit.py`：UTC时间和SHA-256审计事件。
- `static/index.html`：最小演示页。
- `tests/`：完整流程、规则和失败测试。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8312
```

默认端口为`8312`，首次启动自动建库。使用`X-Actor`和`X-Role`请求头传递身份。

## 主要接口

- `GET /health`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `GET /api/items/{id}/measurements`：列出事件下的全部检测（复测）记录
- `POST /api/items/{id}/measurements`：追加检测记录，字段为`measured_at`（可省略，默认当前UTC时间）、`dose`、`conclusion`（`pending`/`normal`/`exceeded`）、`measured_by`
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/audit`

允许角色：dosimetrist, radiation_officer, health_physicist, viewer。剂量与调查水平之比决定升级程度，超过阈值必须进入调查；更正剂量不能覆盖已确认审计记录。

## 复测与判定规则

- 检测记录只追加、不修改：每次复测保存检测时间、剂量、结论和检测人，早先确认的值始终留在历史中。
- 最后一次检测结论用于计算优先级（`priority`）、调查门槛（`escalation_required`）和报告期限（`deadline_hours`/`remaining_hours`）；无检测记录时沿用登记剂量。结论为`exceeded`即达调查门槛，`normal`即使剂量数值超限也不升级。
- 复测没收齐（没有检测记录，或最后一次结论为`pending`）时，事件停留在`reviewing`，不能转入调查。
- 进入关闭前必须存在已关闭的`medical_follow_up`普通记录，否则医学随访未完成不能关闭。
- `GET /api/items`按剩余时间（`remaining_hours`）从短到长排序，已关闭事件排在最后。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
