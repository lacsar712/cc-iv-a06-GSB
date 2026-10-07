# 光伏组串IV扫描台

扫描员提交组串开路电压、短路电流与填充因子。写入后走 PostgreSQL 通知通道叫醒独立工人，工人不轮询空转。填充因子不低于 0.72 为合格，否则衰减。页面是 Vue 3。

## 技术栈

- 后端：Litestar、Uvicorn、psycopg 同步写入
- 工人：`LISTEN/NOTIFY` 唤醒后认领
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
| scanner | scan123456 | 可提交 |
| watcher | watch123456 | 只读 |

## 启动

```bash
cd projects/22-pv-string-iv-scan
docker compose up --build
```

健康检查：`GET http://localhost:8202/api/health`

## 种子

| 组串 | 填充因子 | 结论 |
|------|----------|------|
| 阵列A-串03 | 0.78 | 合格 |
| 阵列B-串11 | 0.61 | 衰减 |

## 积雪覆盖封锁窗

积雪覆盖期内对应阵列的 IV 扫描**整体拒收**，不进 `iv_scans`，HTTP 返回 423 并写明"等积雪消完再收"。

- 顶栏「积雪窗」专页配置：选阵列、填覆盖下限（0–1）与每日起止钟点（终止早于起始即跨午夜窗），下方挂封锁痕迹。
- 阵列按前缀匹配：窗配在 `阵列A` 时，`阵列A-串03` 等该阵列所有组串都被挡住；更具体的阵列窗优先。
- 是否落在覆盖期**只由数据库后台钟点 `LOCALTIME` 判定**（库时区固定 `Asia/Shanghai`，见 docker-compose 的 `PGTZ`）；浏览端本机钟不参与判定，改本机钟绕不过封锁。专页顶部显示后台当前钟点供对照。
- 拒收与封锁痕迹**同批落账**：先在同一连接写 `snow_blocks` 并提交，再回 423；痕迹写不进去单子也不会被放行。痕迹快照当时的阵列、载荷与原因，删窗不删痕迹（`window_id` 置空）。
- 观察员 watcher 能看窗、看痕迹、看后台钟点，但不能配置/改/删窗，也不能提交扫描；把窗的钟点改离当前时刻后再提交即恢复 201 受理。

接口（均需登录，写操作仅 writer）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/snow/clock` | 后台钟点、日期与数据库时区 |
| GET/POST | `/api/snow/windows` | 列窗 / 配窗 |
| PUT/DELETE | `/api/snow/windows/{id}` | 改钟点（挪开覆盖期）/ 删窗 |
| GET | `/api/snow/blocks` | 封锁痕迹 |

