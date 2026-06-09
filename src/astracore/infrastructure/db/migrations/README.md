# Database Migrations

每个 `.sql` 文件对应一次数据库变更，由 `init_db` 在启动时自动按文件名顺序执行，执行记录写入 `_schema_migrations` 表（幂等）。

## 命名规则

```
<序号>_<描述>.sql
```

示例：`002_add_projects_tags.sql`

序号建议三位数字，确保排序正确。

## 编写规范

- 每条语句以 `;` 结尾
- 行注释使用 `--`，会在执行前自动剥离
- 一个文件只做一件事，保持原子性
- **已提交的迁移文件禁止修改**，需要回退请新建文件

## 示例

```sql
-- 为 conversations 表添加标签字段
ALTER TABLE conversations ADD COLUMN tags TEXT NOT NULL DEFAULT '';
```

## 注意事项

- 迁移仅用于 `CREATE TABLE` 之后的**增量变更**；初始表结构由 SQLAlchemy 模型的 `Base.metadata.create_all` 负责
- SQLite 不支持 `DROP COLUMN`（3.35 以下）和修改列类型，复杂重构需借助临时表
