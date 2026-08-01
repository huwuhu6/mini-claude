package com.app.model;

public class UserContext {
    // 目标修改点 1：成员变量
    private String user_token;

    public UserContext(String user_token) {
        // 目标修改点 2：构造函数参数与赋值
        this.user_token = user_token;
    }

    // 目标修改点 3：Getter
    public String getUserToken() {
        return this.user_token;
    }

    // 目标修改点 4：Setter
    public void setUserToken(String user_token) {
        this.user_token = user_token;
    }
}