› fastapi的作用


• FastAPI 在这个项目里是 后端 API 服务层，作用是把 SQLite 里的数据和运行时观测信息暴露给前端。

  整体关系是：

  SQLite 数据库
      ↑↓
  SQLAlchemy / ORM
      ↑↓
  FastAPI 后端
      ↑↓
  HTTP REST API + WebSocket
      ↑↓
  React 前端

  在你的项目里，FastAPI 入口是：

  src/emergence_world/api/app.py:21

  CLI 启动入口是：

  src/emergence_world/cli.py:48

  启动命令：

  world serve --host 127.0.0.1 --port 8000 --cors-origins http://127.0.0.1:5173

  它具体做什么

  1. 启动 HTTP 服务
     前端不能直接读 SQLite 文件，所以需要 FastAPI 提供接口。

  2. 连接数据库
     在 create_app() 里创建 SQLAlchemy engine/session：

     engine = create_sync_database_engine(sync_sqlite_url(database))
     app.state.session_factory = create_sync_session_factory(engine)

  3. 自动迁移数据库
     启动时默认执行 Alembic migration：

     _migrate(database)

  4. 提供 REST API
     例如现有 trace 接口：

     GET /api/v1/traces
     GET /api/v1/traces/{command_id}
     GET /api/v1/traces/{command_id}/spans
     GET /api/v1/traces/{command_id}/tool-calls

  5. 提供 WebSocket 实时连接
     例如：

     ws://127.0.0.1:8000/ws/v1/traces

     用来通知前端有新的 trace 事件。

  6. 处理 CORS
     当前前端 Vite 跑在 5173，后端跑在 8000，浏览器跨端口访问需要 CORS：

     CORSMiddleware(...)

  为什么不能前端直接查 SQL

  浏览器里的 React 代码不能安全、稳定地直接打开本地 emergence_world.db。所以要通过 FastAPI：

  前端请求 /api/v1/traces
  FastAPI 查询 SQLite
  FastAPI 返回 JSON
  前端渲染页面

  所以，FastAPI 的核心作用就是：

  把后端数据库和 world runtime 转换成前端能访问的 JSON API / 实时事件流。