from app.schemas.agent import ModelGenerateRequest
from app.adapters.model import ModelAdapter
from app.services.knowledge_service import search_knowledge

async def run_agent(
    db, 
    model_adapter: ModelAdapter,
    page_text: str, 
    user_input: str, 
    feedback_type: str, 
    age_band: str = "AGE_3_4"
) -> dict:
    """
    引导工作流主函数，依赖外部的 db 连接和 model_adapter
    """
    # 1. 跳过逻辑
    if feedback_type == "SKIPPED":
        return {
            "status": "FALLBACK",
            "outputText": "",
            "strategyType": "SKIP",
            "sources": []
        }

    # 2. 简易安全拦截
    dangerous_words = ["火", "电", "刀", "打"]
    if any(word in user_input for word in dangerous_words):
        return {
            "status": "BLOCKED",
            "outputText": "这个问题我们请爸爸妈妈来一起说好吗？",
            "strategyType": "SAFETY_BLOCK",
            "sources": []
        }

    # 3. 真实查库：知识检索
    knowledge = search_knowledge(db, age_band, "GUIDANCE")
    if not knowledge["has_results"]:
        return {
            "status": "FALLBACK",
            "outputText": "这个故事里，你最喜欢谁呀？",
            "strategyType": "NO_KNOWLEDGE_FALLBACK",
            "sources": []
        }

    # 4. 组装 Prompt (把长文本放进 context_text，短问题放进 user_input)
    knowledge_text = "\n".join([k["content"] for k in knowledge["items"]])
    system_prompt = "你是一个温柔、专业的儿童阅读引导助手。"
    context_text = f"【知识点】{knowledge_text}\n【故事当前页】{page_text}"

    # 5. 调用队友封装好的 ModelAdapter
    request = ModelGenerateRequest(
        system_prompt=system_prompt,
        context_text=context_text,      # 参考资料放这里
        user_input=user_input           # 孩子的话放这里，保证不超过 200 字
    )
    result = await model_adapter.generate(request)
    print(f"[DEBUG] 模型返回状态: success={result.success}, error={result.error_code}") # 新增这行
    # 6. 处理降级（ModelAdapter 已经处理了超时和异常，返回 success=False）
    if not result.success:
        return {
            "status": "FALLBACK",
            "outputText": "我们先看看画面上有什么吧？",
            "strategyType": "TIMEOUT_FALLBACK",
            "sources": []
        }

    # 7. 成功返回
    return {
        "status": "GENERATED",
        "outputText": result.text,
        "strategyType": "CONTEXT_QUESTION",
        "sources": [{"knowledgeItemId": k["knowledge_item_id"], "sourceTitle": k["source_title"]} for k in knowledge["items"]]
    }