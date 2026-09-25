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
- `GET /api/items`（未关闭事件按报告期限剩余时间升序，已关闭排在最后）
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `GET /api/items/{id}/measurements`：事件下的复测检测记录历史
- `POST /api/items/{id}/measurements`：追加一次复测，字段为`measured_at`（ISO 8601）、`dose`、`conclusion`、`measured_by`
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/audit`

允许角色：dosimetrist, radiation_officer, health_physicist, viewer。复测结论取`normal`/`exceeded`/`inconclusive`，只追加不改写，早先确认的值继续留在历史里；**末次复测**的剂量与结论用于计算优先级、调查门槛和报告期限（事件上同时保留`original_quantity`原始剂量）。复测未收齐（无检测或末次结论为`inconclusive`）时事件停在复核中，不能进入调查；关闭前必须存在一条已关闭的`medical_follow_up`记录，即医学随访完成，且无其他未关闭事项。剂量与调查水平之比决定升级程度，更正剂量不能覆盖已确认审计记录。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
