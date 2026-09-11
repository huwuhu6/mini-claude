package com.example;

public final class Invoice {
    public static int payableCents(int subtotalCents, int discountPercent) {
        if (subtotalCents < 0 || discountPercent < 0 || discountPercent > 100) {
            throw new IllegalArgumentException("invalid invoice");
        }
        return subtotalCents - (subtotalCents * discountPercent / 100) - 25;
    }
}
