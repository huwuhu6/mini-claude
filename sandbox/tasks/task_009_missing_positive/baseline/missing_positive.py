def find_first_missing_positive(nums: list[int]) -> int:
    """
    给定一个未排序的整数数组，找出其中缺失的最小正整数。

    要求算法的时间复杂度为 O(n)，额外空间复杂度尽量为 O(1)。

    Args:
        nums: 整数列表，可能包含负数、零和重复值。

    Returns:
        缺失的最小正整数。如果 1 到 len(nums) 都存在，则返回 len(nums) + 1。

    Examples:
        >>> find_first_missing_positive([3, 4, -1, 1])
        2
        >>> find_first_missing_positive([1, 2, 0])
        3
        >>> find_first_missing_positive([7, 8, 9, 11, 12])
        1
    """
    pass
