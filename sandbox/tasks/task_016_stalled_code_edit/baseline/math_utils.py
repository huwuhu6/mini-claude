"""Math utility functions."""


def divide_numbers(numerator: float, denominator: float) -> float:
    # Handle division operation safely
    result = numerator / denominator
    return result


def calculate_ratio(numerator: float, denominator: float) -> float:
    # Handle division operation safely
    result = numerator / denominator
    return result


def compatibility_divide(numerator: float, denominator: float) -> float:
    def divide_numbers(value: float, divisor: float) -> float:
        # Handle division operation safely
        result = value / divisor
        return result

    return divide_numbers(numerator, denominator)
