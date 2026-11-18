from app.schemas.agent import ModelGenerateRequest
from app.adapters.model import ModelAdapter

async def run_agent(
    model_adapter: ModelAdapter,
    page_text: str, 
    user_input: str, 
    feedback_type: str, 
    knowledge: dict  # 改为接收查好的知识，不在这里查库
) -> dict:
    
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

    # 3. 判断是否有知识
    if not knowledge["has_results"]:
        return {
            "status": "FALLBACK",
            "outputText": "这个故事里，你最喜欢谁呀？",
            "strategyType": "NO_KNOWLEDGE_FALLBACK",
            "sources": []
        }

    # 4. 根据反馈构造不同 Prompt
    knowledge_text = "\n".join([k["content"] for k in knowledge["items"]])
    context_text = f"【知识点】{knowledge_text}\n【故事当前页】{page_text}"
    
    if feedback_type == "UNDERSTOOD":
        system_prompt = "你是一个温柔、专业的儿童阅读引导助手。孩子理解了当前内容，请提出更深一步的启发式问题。"
    elif feedback_type == "NOT_UNDERSTOOD":
        system_prompt = "你是一个温柔、专业的儿童阅读引导助手。孩子没听懂，请用更简单的封闭式或回忆式问题（如“你看画面上有什么？”）引导他。"
    else:
        system_prompt = "你是一个温柔、专业的儿童阅读引导助手。"

    # 5. 调用模型
    request = ModelGenerateRequest(
        system_prompt=system_prompt,
        context_text=context_text,
        user_input=user_input
    )
    result = await model_adapter.generate(request)

    # 6. 处理降级
    if not result.success:
        return {
            "status": "FALLBACK",
            "outputText": "我们先看看画面上有什么吧？",
            "strategyType": "TIMEOUT_FALLBACK",
            "sources": []
        }

    # 7. 输出安全校验（AI-07）
    if any(word in result.text for word in dangerous_words):
        return {
            "status": "BLOCKED",
            "outputText": "这个问题我们请爸爸妈妈来一起说好吗？",
            "strategyType": "SAFETY_BLOCK",
            "sources": []
        }

    # 8. 补全最后的返回
    return {
        "status": "GENERATED",
        "outputText": result.text,
        "strategyType": "CONTEXT_QUESTION",
        "sources": [{"knowledgeItemId": k["knowledge_item_id"], "sourceTitle": k["source_title"]} for k in knowledge["items"]]
    }