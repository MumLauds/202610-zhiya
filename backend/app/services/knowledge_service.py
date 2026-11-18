def search_knowledge(db, age_band: str, task_type: str) -> dict:
    query = """
        SELECT id, item_key, content, source_title, source_ref
        FROM knowledge_items
        WHERE status = 'APPROVED'
          AND (age_band = ? OR age_band IS NULL)
          AND EXISTS (
              SELECT 1 FROM json_each(task_types_json) 
              WHERE UPPER(value) = UPPER(?)
          )
        LIMIT 5
    """
    rows = db.execute(query, (age_band, task_type)).fetchall()
    items = [{
        "knowledge_item_id": row["id"],
        "item_key": row["item_key"],
        "content": row["content"],
        "source_title": row["source_title"],
        "source_ref": row["source_ref"]
    } for row in rows]
    
    return {"items": items, "has_results": len(items) > 0}