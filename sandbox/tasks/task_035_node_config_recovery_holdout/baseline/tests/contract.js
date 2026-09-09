const assert = require("node:assert/strict");
const path = require("node:path");
const { loadService } = require("../bin/launch");
const value = loadService(path.join(__dirname, "..", "config", "service.json"));
assert.equal(value.service, "orders");
assert.equal(value.port, 8080);
assert.equal(value.retries, 3);
console.log("PASS node config");
