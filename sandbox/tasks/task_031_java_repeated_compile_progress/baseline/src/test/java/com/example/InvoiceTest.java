package com.example;

public final class InvoiceTest {
    public static void main(String[] args) {
        if (Invoice.payableCents(1000, 10) != 875) {
            throw new AssertionError("invoice total mismatch");
        }
        if (Invoice.payableCents(500, 0) != 475) {
            throw new AssertionError("invoice fee mismatch");
        }
        System.out.println("PASS java invoice");
    }
}
