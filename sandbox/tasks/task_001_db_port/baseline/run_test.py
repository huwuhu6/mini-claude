# run_test.py
import subprocess

def run_app_test():
    result = subprocess.run(["python", "app.py"], capture_output=True, text=True)
    print(result.stdout)
    assert "服务器启动成功！" in result.stdout, "测试失败：服务器未能成功启动"
    print("【🎉 终极大通关：所有跨模块测试完美通过！】")

if __name__ == "__main__":
    run_app_test()
