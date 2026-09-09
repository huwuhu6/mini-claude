const assert = require("node:assert/strict");
const { total, payable } = require("../src/discount");
const items = [{ price: 100, quantity: 2 }, { price: 50, quantity: 1 }];
assert.equal(total(items), 250);
assert.equal(payable(items, "VIP"), 225);
assert.equal(payable(items, "NONE"), 250);
console.log("PASS node invoice");
