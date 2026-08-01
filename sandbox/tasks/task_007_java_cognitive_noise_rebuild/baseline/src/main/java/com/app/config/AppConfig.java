package com.app.config;

public class AppConfig {
    // 故意留下的硬编码字符串，用来测试 count_only 是否会把它和变量混淆
    public static final String AUTH_TOKEN_KEY = "user_token";

    public void printConfig() {
        System.out.println("Config key is: " + AUTH_TOKEN_KEY);
    }
}