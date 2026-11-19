def search_knowledge(db, age_band: str, task_type: str, query_text: str = "") -> dict:
    # 构造模糊匹配的关键词格式
    keyword_pattern = f"%{query_text}%" if query_text else "%"
    
    query = """
        SELECT id, item_key, content, source_title, source_ref
        FROM knowledge_items
        WHERE status = 'APPROVED'
          AND (age_band = ? OR age_band IS NULL)
          AND EXISTS (
              SELECT 1 FROM json_each(task_types_json) 
              WHERE UPPER(value) = UPPER(?)
          )
          AND (content LIKE ? OR source_title LIKE ? OR item_key LIKE ?)
        LIMIT 5
    """
    
    # 执行查询，传入年龄、任务类型和三个模糊匹配参数
    rows = db.execute(query, (age_band, task_type, keyword_pattern, keyword_pattern, keyword_pattern)).fetchall()
    
    items = [{
        "knowledge_item_id": row["id"],
        "item_key": row["item_key"],
        "content": row["content"],
        "source_title": row["source_title"],
        "source_ref": row["source_ref"]
    } for row in rows]
    
    return {"items": items, "has_results": len(items) > 0}