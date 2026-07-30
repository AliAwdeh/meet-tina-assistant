from sqlalchemy import Engine, inspect, text


def ensure_runtime_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "tasks" in table_names:
        task_columns = {column["name"] for column in inspector.get_columns("tasks")}
        if "project_id" not in task_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE tasks ADD COLUMN project_id VARCHAR(36)"))
