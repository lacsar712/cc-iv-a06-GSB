# 光伏组串IV扫描台

扫描员提交组串开路电压、短路电流与填充因子。写入后走 PostgreSQL 通知通道叫醒独立工人，工人不轮询空转。填充因子不低于 0.72 为合格，否则衰减。页面是 Vue 3。

积雪覆盖期内可对阵列设置封锁窗：窗内该阵列送来的单一律拒收，拒收单与封锁痕迹**在同一数据库事务内同批落库**（缺一边整题作废）；是否落在窗内只认后台数据库钟点，不看浏览器本机钟。

## 技术栈

- 后端：Litestar、Uvicorn、psycopg 同步写入
- 工人：`LISTEN/NOTIFY` 唤醒后认领（只认领 pending，拒收单永不处理）
- 前端：Vue 3、Vite、nginx 反代 `/api`

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3202 |
| 接口 | http://localhost:8202 |
| PostgreSQL | localhost:54402（库名 `pvivscan`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| scanner | scan123456 | 可提交、可配置积雪窗 |
| watcher | watch123456 | 只读（扫描单、积雪窗、封锁痕迹都能看，不能改） |

## 启动

```bash
docker compose up --build
```

健康检查：`GET http://localhost:8202/api/health`

## 积雪封锁窗

顶栏进入「积雪窗」专页：选阵列、填覆盖下限（%）与起止钟点（0-23 整点，起始大于结束表示跨零点，相等表示全天）。

- 窗内提交该阵列（`组串编号 = 阵列` 或以 `阵列-` 开头）的扫描单 → `409`，返回「等积雪消完再收」。
- 拒收单写入 `iv_scans(status='rejected')`、封锁痕迹写入 `snow_blocks`，两条记录在同一事务提交。
- 判窗时刻取自数据库 `NOW()`（钟点为 UTC 0-23），接口回传 `server_time/server_hour` 仅作展示，前端不参与判定。
- 把覆盖期「挪开」（PATCH 起止钟点）或删除（软删）后，再送即成功（201）。

| 接口 | 方法 | 权限 |
|------|------|------|
| `/api/snow/windows` | GET | 登录 |
| `/api/snow/windows` | POST | scanner |
| `/api/snow/windows/{id}` | PATCH | scanner |
| `/api/snow/windows/{id}` | DELETE | scanner |
| `/api/snow/blocks` | GET | 登录 |

## 种子

| 组串 | 填充因子 | 结论 |
|------|----------|------|
| 阵列A-串03 | 0.78 | 合格 |
| 阵列B-串11 | 0.61 | 衰减 |
