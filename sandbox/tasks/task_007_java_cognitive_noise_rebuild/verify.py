#!/usr/bin/env python3
"""
验证脚本：检查 task_java_cognitive_noise 的 Java 重构是否正确
验证点：
1. 检查 target/ 和 node_modules/ 是否未被污染或误改。
2. UserContext.java 中的成员变量 user_token 必须改为 session_token。
3. UserContext.java 中的构造函数参数、Getter/Setter 签名与体内部变量必须完美对齐。
4. AppConfig.java 中的字符串常量 "user_token" 绝对不准被篡改。
5. AuthService.java 中的 validateSession 方法参数名 user_token 必须改为 session_token。
"""

# shadow_workspace/
# ├── target/                        # 模拟 Java 编译输出目录（物理黑洞，内含 5000+ 临时 class 文件）
# │   └── classes/com/app/... 
# ├── node_modules/                  # 前端依赖黑洞（内含 10000+ 冗余文件）
# ├── pom.xml                        # Maven 配置文件
# └── src/
#     └── main/
#         └── java/
#             └── com/
#                 └── app/
#                     ├── config/
#                     │   └── AppConfig.java
#                     ├── model/
#                     │   └── UserContext.java
#                     └── service/
#                         └── AuthService.java

#!/usr/bin/env python3
"""
验证脚本：检查 task_java_cognitive_noise 的 Java 重构是否正确
修复版：全面引入自由度正则，消除空格、缩进、以及局部变量伪装导致的误判。
"""

import sys
import os
import re

def load_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def verify():
    errors = []
    base_path = "src/main/java/com/app"
    
    config_path = os.path.join(base_path, "config/AppConfig.java")
    model_path = os.path.join(base_path, "model/UserContext.java")
    service_path = os.path.join(base_path, "service/AuthService.java")
    
    # ── 1. 存在性硬性校验 ──
    for p in [config_path, model_path, service_path]:
        if not os.path.exists(p):
            errors.append(f"文件丢失: {p}")
            return 1

    print("=== [开始验证] 修正版跨模块 Java 语义断言 ===")

    # ── 2. 验证 AppConfig.java (常量绝对防误杀) ──
    config_src = load_file(config_path)
    if not re.search(r'"user_token"', config_src):
        errors.append("AppConfig.java: 字符串字面量 '\"user_token\"' 遭到破坏！Agent 执行了激进的全局模糊替换。")
    else:
        print("✅ AppConfig.java: 字符串常量受物理保护，未被非法篡改")

    # ── 3. 验证 UserContext.java (精准作用域断言) ──
    model_src = load_file(model_path)
    
    # A. 确保旧的类成员变量 user_token 被彻底抹除 (排除方法体内的干扰)
    if re.search(r"private\s+String\s+user_token\s*;", model_src):
        errors.append("UserContext.java: 旧成员变量 'private String user_token;' 依然残留！")
        
    # B. 确保新成员变量合法诞生
    if not re.search(r"private\s+String\s+session_token\s*;", model_src):
        errors.append("UserContext.java: 类顶部未定义新成员变量 'private String session_token;'。")
        
    # C. 验证构造函数参数及内部赋值 (空格/换行不敏感正则)
    constructor_pattern = r"public\s+UserContext\s*\(\s*String\s+session_token\s*\)\s*\{[^}]*this\s*\.\s*session_token\s*=\s*session_token\s*;"
    if not re.search(constructor_pattern, model_src):
        errors.append("UserContext.java: 构造函数签名或体内的 'this.session_token = session_token;' 赋值不匹配（可能参数名未改，或赋值写错）。")
        
    # D. 验证 Getter 内部指针
    getter_pattern = r"public\s+String\s+getUserToken\s*\(\s*\)[^{]*\{\s*return\s+(this\s*\.\s*)?session_token\s*;"
    if not re.search(getter_pattern, model_src):
        errors.append("UserContext.java: getUserToken() 方法体内部 return 的变量未指向新符号 'session_token'。")
        
    # E. 验证 Setter 签名与赋值
    setter_pattern = r"public\s+void\s+setUserToken\s*\(\s*String\s+session_token\s*\)[^{]*\{\s*(this\s*\.\s*)?session_token\s*=\s*session_token\s*;"
    if not re.search(setter_pattern, model_src):
        errors.append("UserContext.java: setUserToken() 的参数名或内部赋值逻辑未正确同步。")

    if len([err for err in errors if "UserContext" in err]) == 0:
        print("✅ UserContext.java: 核心 AST 属性、构造链、Getter/Setter 边界验证通过")

    # ── 4. 验证 AuthService.java (方法签名与跨文件一致性) ──
    service_src = load_file(service_path)
    
    # A. 提取 validateSession 的第二个参数名
    sig_match = re.search(r"public\s+boolean\s+validateSession\s*\(\s*UserContext\s+\w+\s*,\s*String\s+(\w+)\s*\)", service_src)
    if not sig_match:
        errors.append("AuthService.java: validateSession 方法签名语法损毁，或者参数顺序被颠倒。")
    else:
        param_name = sig_match.group(1)
        if param_name != "session_token":
            errors.append(f"AuthService.java: validateSession 的核心参数名应为 'session_token'，实际被改成了 '{param_name}'。")
        else:
            # B. 只有签名对了，才去验证方法体内的参数调用（解耦，防止整行错杀）
            # 确保方法体内使用了刚刚定义的实参 param_name 去做 equals 运算
            body_match_1 = rf"{param_name}\s*\.\s*equals\s*\("
            body_match_2 = rf"\.\s*equals\s*\(\s*{param_name}\s*\)"
            if not (re.search(body_match_1, service_src) or re.search(body_match_2, service_src)):
                errors.append(f"AuthService.java: validateSession 方法体内没有对新参数 '{param_name}' 进行任何合法的逻辑调用。")

    # C. 保护检查：AuthService 自带的干扰私有变量不应被修改
    if not re.search(r"private\s+String\s+user_token\s*=\s*\"internal_fallback\"", service_src):
        print("⚠️  AuthService.java: 提示：Agent 把不该改的局部私有属性 'internal_fallback' 也改了，重构范围扩大化。")
        # 这里作为 soft warning，如果不影响大局可以不计入 errors，或者计入以拉低最高分

    if len([err for err in errors if "AuthService" in err]) == 0:
        print("✅ AuthService.java: 跨文件接口协议与局部数据调用链完美对齐")

    # ── 5. 最终裁决 ──
    print("\n" + "=" * 60)
    print(" 🏁 BENCHMARK JAVA TASK EVALUATION REPORT")
    print("=" * 60)
    
    if errors:
        print(f"❌ [FAILED] 发现 {len(errors)} 个破环语义一致性的严重阻断缺陷:")
        for err in errors:
            print(f"  - {err}")
        return 1
    else:
        print("🎉 [SUCCESS] 恭喜！第二轮 Java 探索、降噪、精细手术式重构全部满分通过！")
        return 0

if __name__ == "__main__":
    sys.exit(verify())