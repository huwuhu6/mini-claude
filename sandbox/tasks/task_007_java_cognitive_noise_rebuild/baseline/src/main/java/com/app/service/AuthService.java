package com.app.service;

import com.app.model.UserContext;

public class AuthService {
    // 干扰点：这里的 user_token 属于 AuthService 的局部逻辑，不应被强制重命名
    private String user_token = "internal_fallback";

    // 目标修改点 5：方法签名中的参数名
    public boolean validateSession(UserContext context, String user_token) {
        if (context == null || user_token == null) {
            return false;
        }
        // 目标修改点 6：方法体内部的参数调用
        return user_token.equals(context.getUserToken());
    }
}