import sys
import io

# 设置标准输出为 UTF-8 编码，解决 Windows GBK 无法打印 emoji 的问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from utils import format_user_data, verify_system_env


def main():
    # 1. 验证系统环境
    verify_system_env()

    # 2. 格式化用户数据
    user_info = format_user_data("Alice", 28)
    print(user_info)

    # 3. 打印通关信息
    print("【🎉 恭喜通关：系统管道全部跑通！】")


if __name__ == "__main__":
    main()
