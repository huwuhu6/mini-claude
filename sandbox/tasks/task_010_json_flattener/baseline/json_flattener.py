def flatten_json(nested: dict, prefix: str = "") -> dict:
    """
    将嵌套的 JSON 对象扁平化为单层字典，键使用点号连接。

    例如：
        {"a": {"b": 1, "c": {"d": 2}}, "e": 3}
    应该被转换为：
        {"a.b": 1, "a.c.d": 2, "e": 3}

    注意：如果值是列表或基本类型（int, str, bool, None），直接作为最终值。

    Args:
        nested: 嵌套的字典对象。
        prefix: 内部递归使用的前缀，调用时不需要传入。

    Returns:
        扁平化后的单层字典。
    """
    pass
