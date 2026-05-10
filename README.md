# 文件去重工具

跨盘符大容量文件去重，专为 60TB+ 视频存储场景设计。

## 核心思路

不是比较所有文件对（O(n²) 不可能），而是三段式逐步求精：

```
所有文件
  ↓ Pass 1: 按文件大小分组（大小唯一的直接排除，覆盖 99%）
同大小文件
  ↓ Pass 2: 快速哈希（只读首尾各 64KB，XXH128）
同快速哈希文件
  ↓ Pass 3: 完整哈希（流式读完整文件，可选跳过）
重复组
```

XXH128 哈希速度 >10GB/s，128 位碰撞概率极低（< 2^-128），非加密场景足够安全。

## 增量机制

首次扫描后文件路径/大小/mtime/哈希全部存入 SQLite。后续扫描只需检查 mtime 是否变化：

- 文件未改 → 跳过，哈希复用上次结果
- 文件新增/修改 → 重新走三段式流程
- 文件已删除 → 标记移除

60TB 数据首次扫描后，后续增量更新通常只需几分钟。

## 项目结构

```
file-analysis/
├── config.py           配置常量（数据库路径、哈希参数、过滤规则）
├── database.py         SQLite 层（WAL 模式、索引优化、增量查询）
├── scanner.py          文件遍历器（生成器模式，内存友好）
├── hasher.py           哈希计算（XXH128 快速哈希 + 完整哈希）
├── deduplicator.py     去重编排（三段式 + 多线程并行）
├── reporter.py         结果输出（CSV / JSON / 交互式）
│
├── dedup.py            CLI 命令行入口
├── server.py           Web 服务端（FastAPI + SSE 实时推送）
├── app.py              桌面客户端（CustomTkinter，Everything 风格）
│
├── static/index.html   Web 前端页面
├── requirements.txt    依赖清单
└── settings.json       桌面客户端配置文件（后缀过滤）
```

## 三种使用方式

### 1. 桌面客户端（推荐）

```bash
pip install customtkinter xxhash
python app.py
```

- 启动即自动增量扫描
- 类似 Everything 的即时搜索
- 一键删除重复文件（保留最旧）
- 设置弹窗勾选后缀过滤

### 2. 命令行

```bash
pip install xxhash

# 全盘扫描
python dedup.py scan --drives D E F G H

# 增量扫描（默认）
python dedup.py scan --drives D E F G H

# 导出报告
python dedup.py report -o dupes.csv

# 交互式查看
python dedup.py report --interactive

# 统计
python dedup.py stats
```

### 3. Web 界面

```bash
pip install xxhash fastapi uvicorn
python server.py
# 浏览器打开 http://localhost:8899
```

## 配置

编辑 `config.py`：

```python
MIN_FILE_SIZE_MB = 200    # 小于此值的文件直接跳过
WORKER_THREADS = 4        # 并行哈希计算线程数
SCAN_EXTENSIONS = {...}   # 仅扫描这些后缀，空集合 = 全部
```

桌面客户端中可通过 **设置** 按钮可视化勾选后缀，自动保存到 `settings.json`。

## 性能参考

| 数据规模 | 首次扫描（快速模式） | 增量更新 |
|---------|-------------------|---------|
| 20 万文件 / ~30TB | ~6 小时 | ~10 分钟 |
| 5 万文件（200MB+ 过滤后） | ~1 小时 | ~2 分钟 |

快速模式仅用文件首尾 128KB 判定重复，跳过完整文件哈希，速度提升 10-20 倍。
勾选"全量哈希"可启用精确模式，但耗时显著增加。

## 依赖

```
xxhash>=3.0.0          # XXH128 哈希算法
customtkinter>=5.0.0   # 桌面客户端 UI（仅 app.py 需要）
fastapi>=0.100.0       # Web 服务（仅 server.py 需要）
uvicorn>=0.23.0        # ASGI 服务器（仅 server.py 需要）
```

## 安全

- 删除操作均有确认弹窗
- 默认跳过 C 盘系统目录
- 不对文件做任何修改（除非手动确认删除）
- 数据库仅存储元数据和哈希值，不存储文件内容
